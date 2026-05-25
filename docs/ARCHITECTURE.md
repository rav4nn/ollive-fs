# Architecture

## High-level

```
                ┌──────────────────────┐
   user ──► UI  │  Next.js (port 3000) │
                └──────────┬───────────┘
                           │ fetch JSON
                           ▼
                ┌──────────────────────────────────────────────┐
                │  FastAPI backend (port 8000)                 │
                │                                              │
                │   /chat ──► chat_service ──► claude_client   │
                │                  │                ▲          │
                │                  │ writes         │ Anthropic │
                │                  ▼                │ SDK       │
                │            sessions + messages   ─┘           │
                │                  │                            │
                │                  ▼                            │
                │            inference_logs (append-only)       │
                │                                              │
                │   ▲ every 60s ▲                              │
                │   │           │                              │
                │   └── ingestion worker ──► inference_stats   │
                │                                              │
                │   /stats reads inference_stats               │
                └──────────────────────┬───────────────────────┘
                                       │
                                       ▼
                              ┌────────────────┐
                              │  PostgreSQL    │
                              └────────────────┘
```

The ingestion worker is an `asyncio.Task` spawned in the FastAPI lifespan, so
the stack is just two long-running processes (backend + frontend) plus
Postgres. No external queue, no Celery, no Redis. The cost is that the worker
shares the backend's process; the win is one container instead of three and a
much simpler runbook.

## Ingestion flow

1. `POST /chat` is called.
2. `chat_service.handle_chat` loads (or creates) the session, appends the user
   message, calls Claude, appends the assistant message, **commits**.
3. After the commit, it builds an `InferencePayload` and calls
   `write_inference_log`, which inserts one row into `inference_logs` in its
   own transaction. `aggregated_at` defaults to `NULL`.
4. The response is returned to the client.
5. Every 60s, `workers/ingestion.run_worker` queries
   `inference_logs WHERE aggregated_at IS NULL`, batches them (≤ 5000 rows per
   tick), groups by `session_id`, and upserts into `inference_stats` via
   Postgres `INSERT ... ON CONFLICT DO UPDATE`. Consumed rows are marked
   `aggregated_at = NOW()` in the same transaction.

Putting the upsert + the mark-as-consumed in **one transaction** is the key
correctness property: either both happen or neither does. If the tick crashes
midway, the next tick picks up where this one left off.

## Logging strategy

> "Never drop a log entry even if the aggregation step fails."

The system guarantees this in three layers:

1. **Raw write durability** — every Claude call produces an
   `inference_logs` row, committed before the HTTP response goes back to the
   client. If Postgres is down, `write_inference_log` falls back to a single
   structured JSON line on stderr (`INFERENCE_LOG_FALLBACK …`) so an operator
   can replay it from container logs.
2. **Idempotent aggregation** — the worker filters `WHERE aggregated_at IS
   NULL`, so a retry never double-counts. The `update ... set aggregated_at`
   and the upsert are in the same transaction.
3. **Bounded backlog** — the worker processes at most `BATCH_LIMIT = 5000`
   rows per tick. If the system is offline for a while, it catches up over
   subsequent ticks instead of trying to do everything at once and OOMing.

The raw `inference_logs` table is **append-only and never deleted by the
application**. Operators can run a retention job (`DELETE WHERE aggregated_at <
NOW() - INTERVAL '30 days'`) without affecting `inference_stats`.

## Schema decisions

**Sessions vs. messages vs. logs.** Three tables, three lifetimes:

- `sessions` is mutable metadata (last_active_at moves forward each turn).
- `messages` is the conversational truth — what we send to the model on the
  next turn.
- `inference_logs` is the audit trail — what *actually* happened on the wire
  (latency, tokens, error). These can diverge: a Claude error means a `messages`
  row for "user" but no "assistant", and an `inference_logs` row with
  `status="error"`.

Splitting them keeps `messages` small and hot-path (chat replay) while
`inference_logs` grows linearly with traffic and can be aged out.

**Stats table.** `inference_stats` is a denormalized rollup. We could
recompute from `inference_logs` on every `/stats` request, but on a busy
deployment that scan is wasted work. Aggregating once per minute trades ≤ 60s
of staleness for O(1) stats reads.

**Indexes.**
- `(session_id, created_at)` on `messages` — drives history loading.
- `(session_id, timestamp)` on `inference_logs` — drives per-session
  forensics.
- `(aggregated_at)` on `inference_logs` — drives the worker's "give me
  unprocessed rows" query. Without this it would be a full table scan every
  60s.

## Scaling considerations

The current design happily handles ~hundreds of chats/sec on a single
Hetzner box. To go further:

- **Stateless backend**: nothing in-process besides the worker is stateful, so
  the backend itself can be replicated behind a load balancer. The worker is
  designed to be safe under multiple concurrent runners — the `update`
  filtering by `aggregated_at IS NULL` is a single SQL statement, and the
  upsert is conflict-safe — but for cleanliness you'd pin it to one replica
  (or use Postgres advisory locks).
- **Postgres**: read replicas for `/stats`, partition `inference_logs` by
  timestamp once it crosses ~10M rows.
- **Decouple ingestion from request path**: move from "insert in request
  handler" to "write to Redis Stream / NATS, worker drains". This makes the
  request latency independent of DB write latency.
- **Streaming Claude responses**: switch `claude_client.chat` to
  `messages.stream`, push tokens via SSE/WebSocket. The inference log can
  still be written once the stream completes, with `latency_ms` capturing
  time-to-first-token and time-to-completion separately.

## Failure handling assumptions

- **Claude API errors** are caught in `ClaudeClient.chat` and surface as a
  `ClaudeResult` with `status="error"`. The chat endpoint returns a friendly
  fallback message and still writes an inference log row with status="error",
  so error rates are visible on the dashboard.
- **DB outage on the request path**: the user-facing chat will 500, but the
  fallback path in `write_inference_log` still tries to emit a structured log
  line so the data isn't gone (operators can rebuild from logs).
- **Worker crash**: the worker's outer `try` catches all exceptions and waits
  one interval before retrying. Since aggregation is filter-by-NULL, the worker
  is naturally restart-safe.
- **Schema drift**: the demo uses `create_all` on startup — this is safe for
  greenfield deploys but you should swap to Alembic before the first migration
  that drops or alters a column.
- **Long Claude calls hold a DB connection**: the chat handler keeps its
  session open across the Claude API call. On a small pool this can stall
  other requests. Mitigation: split into two short DB transactions (one
  before the API call, one after) — left as a follow-up to keep the demo
  readable.

## Trade-offs we made

| Choice | Win | Lose |
| --- | --- | --- |
| Worker inside backend process | One container, simple ops | Can't scale worker independently |
| `create_all` instead of Alembic | No tooling to learn for a demo | Risky for schema changes |
| Postgres rollup table | Cheap `/stats` reads | ≤ 60s staleness |
| `localStorage` session id | No auth needed to "resume" | Clearing cookies loses your history |
| Single shared port for API + UI behind Caddy in prod | Fewer DNS records, simpler CORS | Reverse-proxy config to maintain |
