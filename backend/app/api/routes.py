from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    MessageOut,
    OverallStats,
    SessionStats,
    SessionSummary,
    StatsResponse,
)
from app.db.models import InferenceStats, Session
from app.db.session import get_db
from app.services.chat_service import (
    get_session_messages,
    handle_chat,
    list_sessions,
)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "down"
    return HealthResponse(status="ok", db=db_status)


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, db: AsyncSession = Depends(get_db)) -> ChatResponse:
    text_out, sid = await handle_chat(db, req.session_id, req.message)
    return ChatResponse(session_id=sid, response=text_out)


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
