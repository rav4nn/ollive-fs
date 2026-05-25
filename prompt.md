Goal: Build and deploy a production-ready LLM inference logging and ingestion system to ollive.hardeep.cv

Build the following end to end:

1. CHATBOT APPLICATION
- Multi-turn conversational chatbot using Claude Sonnet API (claude-sonnet-4-20250514)
- FastAPI backend with /chat endpoint accepting {session_id, message} and returning {response, session_id}
- Conversation history maintained per session_id in PostgreSQL
- React or Next.js frontend with clean chat UI, session persistence, message history display

2. INFERENCE LOGGING LAYER
Every LLM call must log to PostgreSQL:
- session_id, timestamp, model_name
- prompt_tokens, completion_tokens, total_tokens
- latency_ms (time from request to response)
- input_text, output_text
- cost_estimate (based on Claude Sonnet pricing)

3. INGESTION PIPELINE
- Background worker that processes raw logs every 60 seconds
- Aggregates per-session stats: total tokens, avg latency, total cost, message count
- Writes aggregated data to a separate inference_stats table
- Exposes a /stats endpoint returning session-level and overall aggregate metrics

4. OBSERVABILITY DASHBOARD
- Simple /dashboard route showing:
  - Total messages sent
  - Average latency across all calls
  - Total tokens consumed
  - Cost estimate
  - Per-session breakdown table
  - Live refresh every 30 seconds

5. DEPLOYMENT
- Deploy to ollive.hardeep.cv on Hetzner behind Cloudflare
- Use Docker Compose: one container for FastAPI backend, one for Next.js frontend, one for Postgres
- Environment variables via .env file
- Health check endpoint at /health

Tech stack: Python, FastAPI, PostgreSQL, Next.js, Docker, Claude API, Cloudflare

Make the UI clean and minimal. The logging and ingestion pipeline is the most important part — it should be robust, handle errors gracefully, and never drop a log entry even if the aggregation step fails.