"""Chat orchestration: load history, call Claude, persist message + inference log."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Message, Session, utcnow
from app.services.claude_client import get_claude_client
from app.services.inference_logger import InferencePayload, write_inference_log
from app.services.pricing import estimate_cost

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a concise, helpful assistant. Keep answers focused and friendly."
)


def new_session_id() -> str:
    return uuid.uuid4().hex


async def _get_or_create_session(db: AsyncSession, session_id: str) -> Session:
    row = await db.get(Session, session_id)
    if row is None:
        row = Session(id=session_id)
        db.add(row)
        await db.flush()
    return row


async def _load_history(db: AsyncSession, session_id: str, limit: int) -> list[Message]:
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    rows.reverse()
    return rows


async def handle_chat(
    db: AsyncSession, session_id: str | None, user_message: str
) -> tuple[str, str]:
    """Process a chat turn. Returns (assistant_text, session_id).

    Behavior:
      - Loads or creates the session.
      - Persists the user message.
      - Calls Claude with the recent history.
      - Persists the assistant message (only if Claude returned text).
      - Writes an inference log row regardless of success/failure.
    """
    settings = get_settings()
    sid = session_id or new_session_id()

    session_row = await _get_or_create_session(db, sid)

    db.add(Message(session_id=sid, role="user", content=user_message))
    await db.flush()

    history = await _load_history(db, sid, settings.max_history_messages)
    messages = [{"role": m.role, "content": m.content} for m in history]

    claude = get_claude_client()
    result = await claude.chat(messages=messages, system=SYSTEM_PROMPT)

    if result.status == "ok" and result.text:
        db.add(Message(session_id=sid, role="assistant", content=result.text))

    session_row.last_active_at = utcnow()
    await db.commit()

    # Log the inference. This uses its own commit/rollback; failures fall back to stderr.
    cost = estimate_cost(result.prompt_tokens, result.completion_tokens)
    payload = InferencePayload(
        session_id=sid,
        provider="anthropic",
        model_name=result.model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
        latency_ms=result.latency_ms,
        input_text=user_message,
        output_text=result.text,
        cost_estimate=cost,
        status=result.status,
        error_message=result.error_message,
    )
    await write_inference_log(db, payload)

    if result.status != "ok":
        return (
            "Sorry, I couldn't generate a response right now. Please try again.",
            sid,
        )
    return result.text, sid


async def list_sessions(db: AsyncSession, limit: int = 50) -> list[Session]:
    stmt = select(Session).order_by(Session.last_active_at.desc()).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_session_messages(db: AsyncSession, session_id: str) -> list[Message]:
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.asc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
