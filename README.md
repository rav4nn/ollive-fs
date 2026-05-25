# ollive — LLM Inference Logging & Ingestion

A small, production-shaped chatbot that captures inference metadata for every LLM
call, rolls it up in a background ingestion worker, and exposes a live
observability dashboard.

- **Backend**: FastAPI, Python 3.11, SQLAlchemy 2 (async), asyncpg
- **Database**: PostgreSQL 16
- **Frontend**: Next.js 14 (App Router, TypeScript)
- **LLM**: pluggable — Anthropic (Claude), DeepSeek, or any OpenAI-compatible
  provider. Switch with `LLM_PROVIDER=anthropic|deepseek|openai`.
- **Container**: one-command `docker compose up`

---

## Quick start

```bash
cp .env.example .env
# Pick ONE provider and set its key:
#   LLM_PROVIDER=deepseek   DEEPSEEK_API_KEY=sk-...
#   LLM_PROVIDER=anthropic  ANTHROPIC_API_KEY=sk-ant-...
#   LLM_PROVIDER=openai     OPENAI_API_KEY=sk-...

docker compose up --build
```

Then open:
- Chat UI:        http://localhost:3000
- Dashboard:      http://localhost:3000/dashboard
- API health:     http://localhost:8000/health
- API stats:      http://localhost:8000/stats

The Postgres tables are created automatically on backend startup.

If port 3000 is taken on your host, set `FRONTEND_PORT=3001` in `.env`.

---

## What it does

1. **Chat** (`POST /chat`) — multi-turn conversation with Claude. Sessions are
   keyed by `session_id` (auto-generated on first message, then persisted in the
   browser's `localStorage` so refreshing the page resumes the conversation).
2. **Inference logging** — every call to Claude writes one row to
   `inference_logs`: tokens, latency, input/output text, status, cost estimate.
   If the DB write fails, the payload is emitted as a single structured JSON
   log line on stderr so an operator can replay it from container logs.
3. **Ingestion worker** — a background asyncio task in the backend process
   wakes every `AGGREGATION_INTERVAL_SECONDS` (default 60s), grabs unprocessed
   rows from `inference_logs`, groups them by `session_id`, and upserts the
   rollup into `inference_stats` using a Postgres `INSERT ... ON CONFLICT DO
   UPDATE`. Consumed rows are marked with `aggregated_at` so we never
   double-count.
4. **Stats endpoint** (`GET /stats`) — returns overall + per-session metrics
   straight from the materialized `inference_stats` table.
5. **Dashboard** — `/dashboard` polls `/stats` every 30s.

---

## Project layout

```
.
├── docker-compose.yml
├── .env.example
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py                 # FastAPI app + lifespan starts worker
│       ├── config.py               # pydantic-settings
│       ├── api/
│       │   ├── routes.py           # /health /chat /stats /sessions
│       │   └── schemas.py          # request/response models
│       ├── db/
│       │   ├── models.py           # SQLAlchemy ORM
│       │   ├── session.py          # async engine + session factory
│       │   └── init_db.py          # create_all on startup
│       ├── services/
│       │   ├── claude_client.py    # Anthropic SDK wrapper, captures latency
│       │   ├── inference_logger.py # DB write w/ stderr-JSON fallback
│       │   ├── chat_service.py     # orchestration
│       │   └── pricing.py          # cost estimator
│       └── workers/
│           └── ingestion.py        # 60s aggregation loop
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   └── src/
│       ├── app/
│       │   ├── layout.tsx
│       │   ├── page.tsx            # chat
│       │   └── dashboard/page.tsx  # dashboard
│       ├── components/
│       │   ├── Chat.tsx
│       │   ├── Dashboard.tsx
│       │   └── TopBar.tsx
│       └── lib/api.ts              # typed API client
└── docs/
    └── ARCHITECTURE.md
```

---

## API

| Method | Path                              | Body                                      | Returns                                |
| ------ | --------------------------------- | ----------------------------------------- | -------------------------------------- |
| GET    | `/health`                         | —                                         | `{status, db}`                         |
| POST   | `/chat`                           | `{session_id?: string, message: string}`  | `{session_id, response}`               |
| GET    | `/sessions`                       | —                                         | list of sessions                       |
| GET    | `/sessions/{id}/messages`         | —                                         | full message history                   |
| GET    | `/stats`                          | —                                         | `{overall, per_session[]}`             |

---

## Schema

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full diagram and
rationale. Summary:

- `sessions` — one row per conversation. `id` is the user-facing handle.
- `messages` — append-only chat log (`role`, `content`, indexed on
  `(session_id, created_at)`).
- `inference_logs` — append-only raw log of every LLM call. The source of
  truth. Has an `aggregated_at` column so the worker can pick up unprocessed
  rows safely.
- `inference_stats` — derived/aggregated table. One row per session, refreshed
  by the worker. Trades a small amount of staleness (≤ 60s) for cheap reads.

---

## Configuration

All config is via environment variables (see `.env.example`):

| Variable | Default | Notes |
| --- | --- | --- |
| `LLM_PROVIDER` | `anthropic` | `anthropic` \| `deepseek` \| `openai` |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | / `claude-sonnet-4-20250514` | when provider=anthropic |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` | / `deepseek-chat` | when provider=deepseek |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | / `gpt-4o-mini` | when provider=openai |
| `OPENAI_BASE_URL` | — | optional override (e.g. any OpenAI-compatible host) |
| `DATABASE_URL` | `postgresql+asyncpg://...` | async URL |
| `AGGREGATION_INTERVAL_SECONDS` | `60` | worker tick |
| `ALLOWED_ORIGINS` | `http://localhost:3000` | comma-separated |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | baked into frontend at build time |
| `FRONTEND_PORT` | `3000` | host port for the frontend container |

Pricing for the cost estimator has built-in defaults per provider (Sonnet 4
$3/$15, DeepSeek $0.27/$1.10, gpt-4o-mini $0.15/$0.60 per MTok). Override with
`PRICE_PER_MILLION_INPUT_TOKENS` / `..._OUTPUT_TOKENS`.

---

## Tests

The backend has a couple of unit-level smoke tests:

```bash
cd backend
pip install -r requirements.txt pytest pytest-asyncio
pytest -v
```

(Requires Python 3.11+. The image is built on `python:3.11-slim`.)

---

## Deploying to `ollive.hardeep.cv`

The whole stack runs from `docker compose`, so deployment is:

1. **Server**: Provision a Hetzner CPX21 (or larger) with Docker installed.
2. **DNS / TLS**: Point `ollive.hardeep.cv` at the server's IP in Cloudflare.
   Enable **Proxy (orange cloud)** and set SSL/TLS mode to **Full**.
3. **Files**: `git clone` this repo on the server.
4. **Env**: `cp .env.example .env`, fill in `ANTHROPIC_API_KEY` and a strong
   `POSTGRES_PASSWORD`. Set:
   - `ALLOWED_ORIGINS=https://ollive.hardeep.cv`
   - `NEXT_PUBLIC_API_BASE_URL=https://ollive.hardeep.cv` (assumes you put the
     API under the same hostname behind a reverse proxy — see below)
5. **Reverse proxy** (Caddy is the simplest):

   ```Caddyfile
   ollive.hardeep.cv {
       reverse_proxy /api/* backend:8000
       reverse_proxy /health backend:8000
       reverse_proxy /chat   backend:8000
       reverse_proxy /stats  backend:8000
       reverse_proxy /sessions* backend:8000
       reverse_proxy * frontend:3000
   }
   ```

   Add Caddy as a fourth service to `docker-compose.yml` if you don't already
   run it on the host. (Cloudflare handles the public TLS; Caddy + the Docker
   network handle the internal routing.)
6. `docker compose up -d --build`

---

## What I'd improve with more time

- **Streaming responses** end-to-end (Anthropic supports SSE; the wrapper would
  need to flush tokens as they arrive, the frontend would need an SSE consumer).
- **Alembic migrations** instead of `create_all` on startup — fine for a demo
  but unsafe for schema evolution in production.
- **Idempotent chat writes** — the `messages` table currently has no
  request_id, so a client retry could double-write a user turn.
- **A real queue** (Redis Streams, NATS) between the request handler and the
  ingestion worker, so the worker can scale horizontally and the request path
  is fully decoupled from log durability.
- **PII redaction** in `input_text` / `output_text` before write. Today they
  are stored verbatim; a regex/LLM-based redaction pass would happen at the
  log boundary.
- **Per-token cost from the API response** — the Anthropic response includes
  `usage.cache_*_tokens` which my estimator ignores.
- **Auth** — currently the API is unauthenticated. Add a session-cookie or
  bearer token check before exposing this publicly.
