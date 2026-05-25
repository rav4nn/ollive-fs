"""Consumer for the inference-log Redis Stream.

Drains the stream in batches, persists each event as an inference_logs row,
then XACKs the batch. Restart-safe because XACK is per-message and
unacknowledged messages stay in the pending entries list (PEL).
"""

from __future__ import annotations

import asyncio
import json
import logging

from redis import asyncio as aioredis

from app.config import get_settings
from app.db.models import InferenceLog
from app.db.session import SessionLocal
from app.services.event_bus import _client, ensure_consumer_group

logger = logging.getLogger(__name__)


async def _persist_batch(messages: list[tuple[str, dict]]) -> None:
    """Insert one batch of events into inference_logs. All-or-nothing per batch."""
    if not messages:
        return
    async with SessionLocal() as db:
        rows: list[InferenceLog] = []
        for _id, fields in messages:
            try:
                payload = json.loads(fields["payload"])
            except Exception:
                logger.exception("malformed payload, dropping id=%s", _id)
                continue
            rows.append(
                InferenceLog(
                    session_id=payload.get("session_id", ""),
                    provider=payload.get("provider", ""),
                    model_name=payload.get("model_name", ""),
                    prompt_tokens=payload.get("prompt_tokens", 0) or 0,
                    completion_tokens=payload.get("completion_tokens", 0) or 0,
                    total_tokens=payload.get("total_tokens", 0) or 0,
                    latency_ms=payload.get("latency_ms", 0) or 0,
                    time_to_first_token_ms=payload.get("time_to_first_token_ms"),
                    input_text=payload.get("input_text", "") or "",
                    output_text=payload.get("output_text", "") or "",
                    cost_estimate=payload.get("cost_estimate", 0.0) or 0.0,
                    status=payload.get("status", "ok") or "ok",
                    error_message=payload.get("error_message"),
                )
            )
        db.add_all(rows)
        await db.commit()


async def run_consumer(stop_event: asyncio.Event) -> None:
    """Main consumer loop. Cancels cleanly when stop_event is set."""
    settings = get_settings()
    client = _client()
    if client is None:
        logger.info("REDIS_URL not set; log consumer disabled (direct-DB path active)")
        return

    await ensure_consumer_group()
    logger.info(
        "Log consumer started: stream=%s group=%s name=%s",
        settings.redis_stream_key,
        settings.redis_consumer_group,
        settings.redis_consumer_name,
    )

    # First pass: drain any pending (un-acked) messages from a previous crash.
    # Then switch to ">" forever to consume new messages as they arrive.
    last_id: str = "0"

    while not stop_event.is_set():
        try:
            result = await client.xreadgroup(
                groupname=settings.redis_consumer_group,
                consumername=settings.redis_consumer_name,
                streams={settings.redis_stream_key: last_id},
                count=settings.redis_batch_size,
                block=settings.redis_block_ms,
            )
        except aioredis.ConnectionError:
            logger.warning("Redis connection lost; retrying in 2s")
            await asyncio.sleep(2)
            continue
        except Exception:
            logger.exception("xreadgroup failed; retrying in 2s")
            await asyncio.sleep(2)
            continue

        # xreadgroup returns [] when there's no data AND we asked with ">",
        # but [(stream, [])] when we asked with a specific id and no pending
        # entries exist. Treat both as "nothing to do this tick".
        messages: list[tuple[str, dict]] = []
        if result:
            _stream_key, messages = result[0]

        if not messages:
            # If we were draining pending and found none, flip to live mode.
            if last_id != ">":
                last_id = ">"
                logger.info("no pending entries; switching to live mode")
            continue

        try:
            await _persist_batch(messages)
            ids = [mid for mid, _ in messages]
            await client.xack(
                settings.redis_stream_key, settings.redis_consumer_group, *ids
            )
            logger.info("consumed batch n=%d", len(messages))
        except Exception:
            # Don't XACK — messages stay in PEL and get reprocessed next loop.
            logger.exception("batch persistence failed; will retry next loop")
            await asyncio.sleep(1)
            continue

        # After processing any batch (pending or live), keep consuming live.
        if last_id != ">":
            last_id = ">"

    logger.info("Log consumer stopped")
