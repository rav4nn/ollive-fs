"""Background ingestion worker.

Every N seconds, scans inference_logs rows that have not yet been aggregated
(`aggregated_at IS NULL`), groups them by session_id, and upserts the rolled-up
metrics into `inference_stats`. Then marks the consumed log rows as aggregated.

Failure handling:
  - Each tick runs inside its own transaction. If aggregation fails for a batch
    the transaction is rolled back, so the inference_logs rows remain
    unaggregated and will be retried on the next tick. Logs are never lost.
  - The loop catches all exceptions to keep ticking — DB outages or transient
    errors don't kill the worker.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import InferenceBucket, InferenceLog, InferenceStats
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

# Cap the batch so a long backlog can't blow up memory.
BATCH_LIMIT = 5_000


async def aggregate_once(db: AsyncSession) -> int:
    """Run one aggregation pass. Returns number of log rows consumed."""
    now = datetime.now(timezone.utc)

    select_pending = (
        select(InferenceLog.id, InferenceLog.session_id)
        .where(InferenceLog.aggregated_at.is_(None))
        .order_by(InferenceLog.id)
        .limit(BATCH_LIMIT)
    )
    pending = (await db.execute(select_pending)).all()
    if not pending:
        return 0

    pending_ids = [row.id for row in pending]
    session_ids = {row.session_id for row in pending}

    from sqlalchemy import case

    error_expr = func.coalesce(
        func.sum(case((InferenceLog.status != "ok", 1), else_=0)), 0
    ).label("error_count")

    agg_stmt = (
        select(
            InferenceLog.session_id.label("session_id"),
            func.count(InferenceLog.id).label("msg_count"),
            func.coalesce(func.sum(InferenceLog.prompt_tokens), 0).label("prompt_sum"),
            func.coalesce(func.sum(InferenceLog.completion_tokens), 0).label("completion_sum"),
            func.coalesce(func.sum(InferenceLog.total_tokens), 0).label("total_sum"),
            func.coalesce(func.sum(InferenceLog.latency_ms), 0).label("latency_sum"),
            func.coalesce(func.sum(InferenceLog.cost_estimate), 0.0).label("cost_sum"),
            error_expr,
            func.min(InferenceLog.timestamp).label("first_ts"),
            func.max(InferenceLog.timestamp).label("last_ts"),
        )
        .where(InferenceLog.id.in_(pending_ids))
        .group_by(InferenceLog.session_id)
    )

    grouped = (await db.execute(agg_stmt)).all()

    # Upsert each session's stats. We add the new batch's totals to whatever
    # was already there for that session.
    for row in grouped:
        values = {
            "session_id": row.session_id,
            "message_count": row.msg_count,
            "total_prompt_tokens": row.prompt_sum,
            "total_completion_tokens": row.completion_sum,
            "total_tokens": row.total_sum,
            "total_latency_ms": row.latency_sum,
            "avg_latency_ms": (row.latency_sum / row.msg_count) if row.msg_count else 0.0,
            "total_cost": float(row.cost_sum),
            "error_count": row.error_count,
            "first_seen_at": row.first_ts,
            "last_seen_at": row.last_ts,
            "updated_at": now,
        }
        stmt = pg_insert(InferenceStats).values(**values)
        # On conflict (existing session) accumulate.
        stmt = stmt.on_conflict_do_update(
            index_elements=[InferenceStats.session_id],
            set_={
                "message_count": InferenceStats.message_count + row.msg_count,
                "total_prompt_tokens": InferenceStats.total_prompt_tokens + row.prompt_sum,
                "total_completion_tokens": InferenceStats.total_completion_tokens
                + row.completion_sum,
                "total_tokens": InferenceStats.total_tokens + row.total_sum,
                "total_latency_ms": InferenceStats.total_latency_ms + row.latency_sum,
                "avg_latency_ms": (
                    (InferenceStats.total_latency_ms + row.latency_sum)
                    / (InferenceStats.message_count + row.msg_count)
                ),
                "total_cost": InferenceStats.total_cost + float(row.cost_sum),
                "error_count": InferenceStats.error_count + row.error_count,
                "last_seen_at": func.greatest(InferenceStats.last_seen_at, row.last_ts),
                "updated_at": now,
            },
        )
        await db.execute(stmt)

    # ---- per-minute time-series buckets (for throughput / latency charts) ----
    bucket_ts = func.date_trunc("minute", InferenceLog.timestamp)
    bucket_stmt = (
        select(
            bucket_ts.label("bucket_ts"),
            func.count(InferenceLog.id).label("msg_count"),
            func.coalesce(func.sum(InferenceLog.total_tokens), 0).label("token_sum"),
            func.coalesce(func.avg(InferenceLog.latency_ms), 0.0).label("avg_latency"),
            func.coalesce(
                func.percentile_cont(0.95).within_group(InferenceLog.latency_ms.asc()), 0.0
            ).label("p95_latency"),
            func.coalesce(func.sum(InferenceLog.cost_estimate), 0.0).label("cost_sum"),
            error_expr,
        )
        .where(InferenceLog.id.in_(pending_ids))
        .group_by(bucket_ts)
    )
    bucketed = (await db.execute(bucket_stmt)).all()
    for b in bucketed:
        stmt = pg_insert(InferenceBucket).values(
            bucket_ts=b.bucket_ts,
            message_count=b.msg_count,
            total_tokens=b.token_sum,
            avg_latency_ms=float(b.avg_latency),
            p95_latency_ms=float(b.p95_latency),
            error_count=b.error_count,
            total_cost=float(b.cost_sum),
        )
        # On conflict (same minute aggregated more than once across ticks)
        # accumulate counts; recompute avg latency using sum/count; take the
        # higher p95 since a true p95 across multiple ticks would need raw
        # samples (close enough for a dashboard).
        new_count_expr = InferenceBucket.message_count + b.msg_count
        new_latency_total = (
            InferenceBucket.avg_latency_ms * InferenceBucket.message_count
            + float(b.avg_latency) * b.msg_count
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[InferenceBucket.bucket_ts],
            set_={
                "message_count": new_count_expr,
                "total_tokens": InferenceBucket.total_tokens + b.token_sum,
                "avg_latency_ms": new_latency_total / new_count_expr,
                "p95_latency_ms": func.greatest(
                    InferenceBucket.p95_latency_ms, float(b.p95_latency)
                ),
                "error_count": InferenceBucket.error_count + b.error_count,
                "total_cost": InferenceBucket.total_cost + float(b.cost_sum),
            },
        )
        await db.execute(stmt)

    # Mark the consumed log rows so we don't double-count them.
    await db.execute(
        update(InferenceLog)
        .where(InferenceLog.id.in_(pending_ids))
        .values(aggregated_at=now)
    )

    await db.commit()
    logger.info(
        "aggregation tick: consumed=%d sessions=%d buckets=%d",
        len(pending_ids), len(session_ids), len(bucketed),
    )
    return len(pending_ids)


async def run_worker(stop_event: asyncio.Event) -> None:
    """Main worker loop. Cancels cleanly when stop_event is set."""
    interval = get_settings().aggregation_interval_seconds
    logger.info("Ingestion worker started, interval=%ds", interval)

    while not stop_event.is_set():
        try:
            async with SessionLocal() as db:
                await aggregate_once(db)
        except Exception:
            logger.exception("Aggregation tick failed; will retry next interval")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
    logger.info("Ingestion worker stopped")
