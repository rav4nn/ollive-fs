"""Inference log writer.

Three-tier durability:
  1. Try Redis Streams (when REDIS_URL is set) — fast publish, consumer
     persists asynchronously.
  2. Fall back to a direct Postgres write if Redis is unavailable or returned
     False.
  3. If the DB write also fails, emit a single JSON line on stderr so an
     operator can replay it from container logs.

The guarantee is "never silently dropped" — at least one of these three
levels will always run, and each one logs on failure.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InferenceLog

logger = logging.getLogger(__name__)


@dataclass
class InferencePayload:
    session_id: str
    provider: str
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    input_text: str
    output_text: str
    cost_estimate: float
    status: str = "ok"
    error_message: str | None = None
    # Only populated for streaming calls.
    time_to_first_token_ms: int | None = None

    def to_json(self) -> str:
        d = asdict(self)
        d["timestamp"] = datetime.now(timezone.utc).isoformat()
        return json.dumps(d, default=str)


async def write_inference_log(db: AsyncSession, payload: InferencePayload) -> None:
    """Persist an inference log. Prefers Redis stream when configured."""
    # Local import to keep event_bus a soft dep at module-load time
    # (in case redis isn't installed in some weird local env).
    from app.services.event_bus import publish

    if await publish(payload):
        return  # consumer will land it in Postgres

    # Redis path unavailable — write straight to DB.
    try:
        row = InferenceLog(
            session_id=payload.session_id,
            provider=payload.provider,
            model_name=payload.model_name,
            prompt_tokens=payload.prompt_tokens,
            completion_tokens=payload.completion_tokens,
            total_tokens=payload.total_tokens,
            latency_ms=payload.latency_ms,
            time_to_first_token_ms=payload.time_to_first_token_ms,
            input_text=payload.input_text,
            output_text=payload.output_text,
            cost_estimate=payload.cost_estimate,
            status=payload.status,
            error_message=payload.error_message,
        )
        db.add(row)
        await db.commit()
    except Exception as exc:
        # Last-resort fallback: structured stderr line so it can be replayed.
        logger.error("INFERENCE_LOG_FALLBACK %s | error=%s", payload.to_json(), exc)
        try:
            await db.rollback()
        except Exception:
            pass
