# Architecture

## High-level

```
                            ┌──────────────────────┐
   user ──► UI               │  Next.js (port 3000) │
                            └──────────┬───────────┘
                                       │ fetch / SSE
                                       ▼
            ┌─────────────────────────────────────────────────────┐
            │  FastAPI backend                                    │
            │                                                     │
            │   POST /chat ──────► chat_service ──► llm_client    │
            │                          │                ▲         │
            │                          │ deltas         │ provider│
            │   POST /chat/stream ─────┘                │ (Anthr. │
            │   (SSE)                                   │  / DSeek│
            │                                           │  / OAI  │
            │                          ▼                │  / Gem.)│
            │            sessions + messages (Postgres) │         │
            │                          │                │         │
            │                          ▼                │         │
            │            inference_logger ──► Redis ────┘         │
            │                              Stream                 │
            │                              ▲                      │
            │                              │ XADD                 │
            │                              │                      │
            │  log_consumer (XREADGROUP) ──┘──► inference_logs    │
            │                                       │             │
            │  ingestion worker ──► inference_stats │             │
            │  (every 60s)        + inference_buckets             │
            │                                                     │
            │  /stats reads inference_stats                       │
            │  /stats/timeseries reads inference_buckets          │
            └─────────────────────────────────────────────────────┘
```

The three long-running async tasks live inside the FastAPI process via the
lifespan context manager: the **log consumer** (drains Redis stream into
Postgres), the **aggregation worker** (per-session and per-minute rollups
every 60s), and uvicorn itself. The trade is one process to operate vs.
separate worker pods. To scale the consumer horizontally, run multiple
backend pods; each gets a unique `REDIS_CONSUMER_NAME` from the k8s downward
API and the Redis consumer group splits the work automatically.

## Ingestion flow

1. `POST /chat` (or `/chat/stream`) calls `handle_chat()` /
   `handle_chat_stream()`, which loads/creates the session, appends the user
   message, calls the LLM, appends the assistant message, **commits**.
2. After the commit, builds an `InferencePayload` and calls
   `write_inference_log()`.
3. `write_inference_log()` tries **three tiers** in order:
   1. `event_bus.publish()` → `XADD inference_logs ...` to Redis (success
      path)
   2. If Redis is unavailable, falls back to a direct `INSERT INTO
      inference_logs ...`
   3. If the DB write also fails, emits a single JSON line on stderr as
      `INFERENCE_LOG_FALLBACK ...` so operators can replay from container
      logs
4. The log consumer (`workers/log_consumer.py`) runs `XREADGROUP` in a loop
   with `block=5000ms`. On a batch arrival, it inserts all rows in one
   transaction, then `XACK`s. Unacked messages stay in the consumer's PEL
   and are reprocessed on the next loop.
5. The aggregation worker (`workers/ingestion.py`) wakes every 60s, scans
   `inference_logs WHERE aggregated_at IS NULL`, groups them by
   `session_id` (for `inference_stats`) and by minute via
   `date_trunc('minute', ts)` (for `inference_buckets`), upserts both via
   Postgres `INSERT ... ON CONFLICT DO UPDATE`, and marks the consumed log
   rows with `aggregated_at = NOW()` — all in one transaction.

## Logging strategy

> "Never drop a log entry even if the aggregation step fails."

Three layers of durability:

1. **Three-tier write path** (Redis → Postgres → stderr JSON) — described
   above. Every successful chat call emits a log somewhere.
2. **Idempotent aggregation** — the worker filters `WHERE aggregated_at IS
   NULL`, so a retry never double-counts. The "upsert stats + buckets" and
   the "mark as consumed" both run in the same transaction.
3. **Bounded backlog** — `BATCH_LIMIT = 5000`. If the system was offline
   for a while, it catches up over multiple ticks instead of trying to
   process everything at once and OOMing.

The `inference_logs` table is **append-only and never deleted by the
application**. Operators can run a retention job (`DELETE WHERE
aggregated_at < NOW() - INTERVAL '30 days'`) without touching
`inference_stats` or `inference_buckets`.

## PII redaction

When `REDACT_PII=true`, `chat_service` calls `pii.maybe_redact()` on the
user message and assistant text **before** they go into the
`InferencePayload`. Originals still flow to the LLM and back — we just
don't keep them in the DB. Patterns covered:

| Type | Placeholder |
| --- | --- |
| Email | `[REDACTED:EMAIL]` |
| Credit card (13–16 digits) | `[REDACTED:CREDIT_CARD]` |
| SSN-like (`xxx-xx-xxxx`) | `[REDACTED:SSN]` |
| Phone (many formats) | `[REDACTED:PHONE]` |
| IPv4 | `[REDACTED:IP]` |

Regex is the cheap first layer. A production system should layer a proper
NER pass on top to catch names, addresses, and free-form PII the regex
can't see.

## Schema decisions

**Three tables, three lifetimes:**

- `sessions` — mutable metadata (`last_active_at` moves forward each turn)
- `messages` — append-only chat history sent back to the model on each
  turn
- `inference_logs` — append-only audit trail of every wire call (latency,
  tokens, error). Diverges from `messages` on errors (one user message,
  zero assistant messages, one error log)
- `inference_stats` — per-session denormalized rollup
- `inference_buckets` — per-minute denormalized time-series, primary key on
  `bucket_ts` (UTC truncated to minute)

Splitting them keeps `messages` small and hot-path. `inference_logs`
grows linearly with traffic and is the table you'd age out.

**Indexes:**

- `(session_id, created_at)` on `messages` — drives history loading
- `(session_id, timestamp)` on `inference_logs` — per-session forensics
- `(aggregated_at)` on `inference_logs` — the worker's "give me unprocessed
  rows" query
- Primary key on `bucket_ts` for `inference_buckets` — orders the
  timeseries query naturally

## Scaling considerations

- **Backend pods**: stateless from the chat-handler POV; the Redis consumer
  group splits log-write work across replicas. Scale with
  `kubectl scale deploy/backend --replicas=N`. The aggregation worker
  inside the lifespan will run on every replica — for now that's fine
  (the SQL is idempotent), but for many replicas you'd want a Postgres
  advisory lock to elect one leader.
- **Postgres**: read replicas for `/stats` / `/stats/timeseries`. Partition
  `inference_logs` by timestamp once it crosses ~10M rows.
- **Redis Streams**: easy first improvement is **consumer-group sharding**
  by `session_id` hash. The stream itself is single-shard until you go to
  Redis Cluster.
- **Streaming**: TTFT is captured and stored; next step would be also
  emitting a per-token timing event so you can chart inter-token latency.

## Failure handling assumptions

- **LLM API errors** are caught in the client wrappers and surface as an
  `LLMResult` with `status="error"`. The chat endpoint returns a friendly
  fallback message and still writes an `inference_logs` row with
  `status="error"`, so error rates are visible on the dashboard.
- **Redis outage**: `publish()` returns False, the caller falls back to a
  direct DB write. No data loss.
- **Postgres outage on the request path**: the user-facing chat will 500.
  The fallback path in `write_inference_log` emits a structured JSON line
  on stderr so the data isn't gone — operators can rebuild from logs.
- **Aggregation worker crash**: the outer try catches everything and waits
  one interval before retrying. Aggregation is filter-by-NULL, so the
  worker is naturally restart-safe.
- **Consumer crash**: in-flight messages stay in the consumer's PEL and
  get reprocessed on restart. Adding `XAUTOCLAIM` would let other
  consumers steal stuck messages from a dead one.
- **Schema drift**: `create_all` + an `_ADDITIVE_MIGRATIONS` list in
  `init_db.py` handles add-column-if-not-exists. Anything more invasive
  (rename, drop, change type) needs Alembic.

## Trade-offs we made

| Choice | Win | Lose |
| --- | --- | --- |
| All long-running tasks inside backend lifespan | One container/process to operate | Worker can't scale independently of HTTP traffic (mitigated: scale backend replicas) |
| Redis Streams (not Kafka/NATS) | Tiny ops surface, fits a single box | Single-shard streams; no cross-region replication out of the box |
| `create_all` + tiny migration hook | No tooling to learn for additive changes | Risky for non-additive schema changes |
| Per-minute buckets vs raw scan on each request | O(1) `/stats/timeseries` | ≤60s staleness |
| Host nginx (not Traefik) + NodePorts | Doesn't disturb the other apps that already use host nginx | Two layers of L7 (nginx + service) instead of one |
| Localhost `localStorage` session id | Trivial "resume", no auth | Clearing site data loses your history |
| `--disable=traefik --disable=servicelb` on k3s | Coexists with host nginx without port conflict | Slightly less idiomatic k8s (no Ingress resource) — manifests still portable, just need a real ingress controller on a multi-app cluster |
