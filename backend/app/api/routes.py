import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    BucketPoint,
    ChatRequest,
    ChatResponse,
    HealthResponse,
    MessageOut,
    OverallStats,
    ProviderInfo,
    ProvidersResponse,
    SessionStats,
    SessionSummary,
    StatsResponse,
    TimeseriesResponse,
)
from app.config import get_settings
from app.db.models import InferenceBucket, InferenceStats, Session
from app.db.session import SessionLocal, get_db
from app.services.chat_service import (
    get_session_messages,
    handle_chat,
    handle_chat_stream,
    list_sessions,
)
from app.services.llm_client import LLMResult, available_providers

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "down"
    return HealthResponse(status="ok", db=db_status)


@router.get("/providers", response_model=ProvidersResponse)
async def providers() -> ProvidersResponse:
    info = available_providers()
    default = get_settings().llm_provider.lower()
    return ProvidersResponse(
        default=default,
        providers=[
            ProviderInfo(name=n, available=v["available"], model=v["model"], is_default=(n == default))
            for n, v in info.items()
        ],
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, db: AsyncSession = Depends(get_db)) -> ChatResponse:
    text_out, sid = await handle_chat(db, req.session_id, req.message, req.provider)
    return ChatResponse(session_id=sid, response=text_out)


def _sse(event: str, data: dict) -> bytes:
    """Format one Server-Sent Events frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Stream the assistant response as SSE.

    Frames:
      event: session   data: {"session_id": "..."}     (once, first)
      event: delta     data: {"text": "..."}            (many, each token chunk)
      event: done      data: {"usage": {...}, "status": "ok"|"error", ...}  (once, last)
    """

    async def gen():
        # Use our own DB session so the generator's lifetime isn't tied to
        # FastAPI's request-scoped Depends teardown (which closes the session
        # when the handler returns, before we've finished streaming).
        async with SessionLocal() as db:
            try:
                async for kind, value in handle_chat_stream(
                    db, req.session_id, req.message, req.provider
                ):
                    if kind == "session":
                        yield _sse("session", {"session_id": value})
                    elif kind == "delta":
                        yield _sse("delta", {"text": value})
                    elif kind == "done":
                        assert isinstance(value, LLMResult)
                        yield _sse(
                            "done",
                            {
                                "status": value.status,
                                "error_message": value.error_message,
                                "usage": {
                                    "prompt_tokens": value.prompt_tokens,
                                    "completion_tokens": value.completion_tokens,
                                    "total_tokens": value.total_tokens,
                                },
                                "latency_ms": value.latency_ms,
                                "time_to_first_token_ms": value.time_to_first_token_ms,
                                "provider": value.provider,
                                "model": value.model,
                            },
                        )
            except Exception as exc:
                yield _sse("done", {"status": "error", "error_message": str(exc)})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable nginx response buffering
            "Connection": "keep-alive",
        },
    )


@router.get("/sessions", response_model=list[SessionSummary])
async def get_sessions(db: AsyncSession = Depends(get_db)) -> list[SessionSummary]:
    rows = await list_sessions(db)
    return [SessionSummary.model_validate(r) for r in rows]


@router.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
async def session_messages(
    session_id: str, db: AsyncSession = Depends(get_db)
) -> list[MessageOut]:
    exists = await db.get(Session, session_id)
    if exists is None:
        raise HTTPException(status_code=404, detail="session not found")
    rows = await get_session_messages(db, session_id)
    return [MessageOut.model_validate(r) for r in rows]


@router.get("/stats/timeseries", response_model=TimeseriesResponse)
async def stats_timeseries(
    minutes: int = 60, db: AsyncSession = Depends(get_db)
) -> TimeseriesResponse:
    """Return per-minute buckets for the last `minutes` minutes (default 60)."""
    from datetime import datetime, timedelta, timezone

    minutes = max(1, min(minutes, 24 * 60))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    rows = (
        await db.execute(
            select(InferenceBucket)
            .where(InferenceBucket.bucket_ts >= cutoff)
            .order_by(InferenceBucket.bucket_ts.asc())
        )
    ).scalars().all()
    return TimeseriesResponse(points=[BucketPoint.model_validate(r, from_attributes=True) for r in rows])


@router.get("/stats", response_model=StatsResponse)
async def stats(db: AsyncSession = Depends(get_db)) -> StatsResponse:
    per_session_rows = (
        await db.execute(
            select(InferenceStats).order_by(InferenceStats.last_seen_at.desc())
        )
    ).scalars().all()

    overall_row = (
        await db.execute(
            select(
                func.count(InferenceStats.session_id),
                func.coalesce(func.sum(InferenceStats.message_count), 0),
                func.coalesce(func.sum(InferenceStats.total_tokens), 0),
                func.coalesce(func.sum(InferenceStats.total_latency_ms), 0),
                func.coalesce(func.sum(InferenceStats.total_cost), 0.0),
                func.coalesce(func.sum(InferenceStats.error_count), 0),
            )
        )
    ).one()

    sessions_count, total_msgs, total_tokens, total_latency, total_cost, error_count = overall_row
    avg_latency = (total_latency / total_msgs) if total_msgs else 0.0

    return StatsResponse(
        overall=OverallStats(
            sessions=sessions_count or 0,
            total_messages=total_msgs or 0,
            total_tokens=total_tokens or 0,
            avg_latency_ms=round(float(avg_latency), 2),
            total_cost=round(float(total_cost or 0.0), 6),
            error_count=error_count or 0,
        ),
        per_session=[SessionStats.model_validate(r) for r in per_session_rows],
    )
