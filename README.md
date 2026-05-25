# ollive — LLM Inference Logging & Ingestion

A production-shaped chatbot that captures every LLM call, ships the metadata
through Redis Streams to a Postgres-backed ingestion pipeline, and surfaces
live throughput / latency / cost / error charts on a small dashboard. Live at
**https://ollive.hardeep.cv** running on a single-node k3s cluster behind
Cloudflare DNS + Let's Encrypt.

## Stack

| Concern | Choice |
| --- | --- |
| Backend | FastAPI + SQLAlchemy 2 (async) + asyncpg |
| Frontend | Next.js 14 (App Router, TypeScript) |
| Database | PostgreSQL 16 |
| Message bus | Redis 7 Streams (event-based ingestion) |
| LLMs | **Anthropic, DeepSeek, OpenAI, Gemini** (provider selected via env) |
| Streaming | Server-Sent Events (`/chat/stream`) |
| Privacy | PII redaction at storage boundary (emails, phones, cards, SSN, IPs) |
| Container | Docker Compose (one-command local) **and** k3s manifests (production) |
| Observability | `/stats` (snapshot), `/stats/timeseries` (per-minute buckets), dashboard with sparklines |

## Quick start (local)

```bash
cp .env.example .env
# Pick a provider and add its key:
#   LLM_PROVIDER=deepseek   DEEPSEEK_API_KEY=sk-...
#   LLM_PROVIDER=anthropic  ANTHROPIC_API_KEY=sk-ant-...
#   LLM_PROVIDER=openai     OPENAI_API_KEY=sk-...
#   LLM_PROVIDER=gemini     GEMINI_API_KEY=...

docker compose up --build
```

Then open:
- Chat UI:    http://localhost:3000
- Dashboard:  http://localhost:3000/dashboard
- API health: http://localhost:8000/health
- API stats:  http://localhost:8000/stats

If host port 3000 is taken, set `FRONTEND_PORT=3001` in `.env`.

## Live deployment

Production runs on a single Hetzner box with **k3s** (k8s without external
load balancer or traefik), behind the existing host nginx for TLS:

```
                       Cloudflare DNS (ollive.hardeep.cv)
                                      │
                                      ▼
                       Host nginx (80/443, Let's Encrypt)
                                      │
                  ┌───────────────────┴───────────────────┐
                  │     proxy_pass to NodePorts           │
                  │     /chat/* /stats/* /sessions/* etc  │
                  │     → 127.0.0.1:30001 (backend)       │
                  │     /                                 │
                  │     → 127.0.0.1:30000 (frontend)      │
                  └───────────────────┬───────────────────┘
                                      ▼
                          k3s NodePort layer
                                      │
            ┌─────────────────────────┼─────────────────────────┐
            │           namespace=ollive                        │
            │                                                   │
            │   frontend ─── backend ──┬── redis (events)      │
            │                          └── postgres (PVC)      │
            └───────────────────────────────────────────────────┘
```

See [deploy/k8s/README.md](deploy/k8s/README.md) for first-install and
re-deploy commands. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the
ingestion-pipeline + failure-handling deep dive.

## What's in the box

| Feature | Where |
| --- | --- |
| Multi-turn chat | `POST /chat` |
| Streaming chat (SSE, token-by-token) | `POST /chat/stream` |
| List / resume / cancel conversations | Sidebar + `localStorage` + AbortController |
| Inference logging (model, tokens, latency, **TTFT**, cost, status) | `inference_logs` table |
| Event-based pipeline | request → Redis Stream → consumer group → Postgres batch insert |
| Per-session rollup | `inference_stats`, updated every 60s |
| Per-minute time-series | `inference_buckets` (msg count, tokens, avg+p95 latency, errors, cost) |
| Throughput / latency / error charts | Dashboard sparklines, refresh every 30s |
| PII redaction at storage boundary | `app/services/pii.py`, gated by `REDACT_PII=true` |
| Three-tier log durability | Redis → Postgres → stderr JSON; **never silently dropped** |
| Multi-provider | `LLM_PROVIDER=anthropic\|deepseek\|openai\|gemini` |
| Per-provider pricing defaults | Anthropic / DeepSeek / OpenAI / Gemini |
| One-command Docker Compose | `docker compose up --build` |
| Kubernetes manifests | `deploy/k8s/` |

## API

| Method | Path | Body | Notes |
| --- | --- | --- | --- |
| GET    | `/health` | — | `{status, db}` |
| POST   | `/chat` | `{session_id?, message}` | Waits for full response |
| POST   | `/chat/stream` | `{session_id?, message}` | SSE: `session`, `delta×N`, `done` |
| GET    | `/sessions` | — | Recent sessions |
| GET    | `/sessions/{id}/messages` | — | Full history for a session |
| GET    | `/stats` | — | Snapshot: overall + per-session |
| GET    | `/stats/timeseries?minutes=N` | — | Per-minute buckets (default 60min) |

## Configuration

All via env (see `.env.example`):

| Variable | Default | Notes |
| --- | --- | --- |
| `LLM_PROVIDER` | `anthropic` | `anthropic` \| `deepseek` \| `openai` \| `gemini` |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | / `claude-sonnet-4-20250514` | |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` | / `deepseek-chat` | |
| `OPENAI_API_KEY` / `OPENAI_MODEL` / `OPENAI_BASE_URL` | / `gpt-4o-mini` / — | |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | / `gemini-2.0-flash` | Uses Google's OpenAI-compatible endpoint |
| `DATABASE_URL` | `postgresql+asyncpg://...` | async URL |
| `REDIS_URL` | `redis://redis:6379/0` | empty disables event bus → synchronous DB path |
| `AGGREGATION_INTERVAL_SECONDS` | `60` | per-session + per-minute rollup tick |
| `ALLOWED_ORIGINS` | `http://localhost:3000` | comma-separated |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | baked into frontend build |
| `REDACT_PII` | `false` | redact emails/phones/cards/SSN/IPs before storage |

## Tests

```bash
cd backend
pip install -r requirements.txt pytest pytest-asyncio
pytest -v
```

Currently covers pricing, schema validation, and PII redaction patterns.

## What I'd still improve

- **Real authentication**: API is unauthenticated. Add a session-cookie or
  bearer token before exposing publicly with real data.
- **Alembic migrations**: `create_all` + a small `ALTER ... IF NOT EXISTS`
  hook works for a demo; production needs Alembic for non-additive changes.
- **Better PII**: regex is the cheap layer. A proper NER pass would catch
  names, addresses, and other things regex can't see.
- **Streaming logs to the dashboard**: today the dashboard polls. A
  Postgres `LISTEN/NOTIFY` or a second Redis stream could push live metrics.
- **cert-manager inside k3s**: today TLS is host nginx + Certbot. Moving the
  cert into the cluster via cert-manager + an Ingress would remove the
  host-nginx step entirely.
