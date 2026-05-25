"""Inference log writer.

Guarantee: a log entry is never silently dropped. If the DB write fails the
payload is emitted to stderr as a single JSON line, so an operator can replay
it from logs.
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

    def to_json(self) -> str:
        d = asdict(self)
        d["timestamp"] = datetime.now(timezone.utc).isoformat()
        return json.dumps(d, default=str)


async def write_inference_log(db: AsyncSession, payload: InferencePayload) -> None:
    """Persist an inference log row. On DB failure, emit JSON to stderr instead."""
    try:
        row = InferenceLog(
            session_id=payload.session_id,
            provider=payload.provider,
            model_name=payload.model_name,
            prompt_tokens=payload.prompt_tokens,
            completion_tokens=payload.completion_tokens,
            total_tokens=payload.total_tokens,
            latency_ms=payload.latency_ms,
            input_text=payload.input_text,
            output_text=payload.output_text,
            cost_estimate=payload.cost_estimate,
            status=payload.status,
            error_message=payload.error_message,
        )
        db.add(row)
        await db.commit()
    except Exception as exc:
        # Fallback path: structured stderr line so it can be replayed from container logs.
        logger.error("INFERENCE_LOG_FALLBACK %s | error=%s", payload.to_json(), exc)
        try:
            await db.rollback()
        except Exception:
            pass
