"""Redis Streams event bus for inference logs.

This module is the *producer* side: the chat handler calls publish() with an
InferencePayload and we put it on a Redis Stream. The consumer side lives in
workers/log_consumer.py.

Design notes:
  - We use Redis Streams (XADD / XREADGROUP) rather than pub/sub because
    streams are persistent: if the worker is down, events accumulate and get
    drained on restart instead of being dropped.
  - One consumer group, one consumer for now. Scaling to N workers is just a
    matter of running N replicas with distinct REDIS_CONSUMER_NAMEs.
  - If Redis is unavailable (or REDIS_URL is empty), publish() returns False
    and the caller falls back to writing directly to Postgres — so the
    pipeline degrades gracefully instead of dropping data.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone

from redis import asyncio as aioredis

from app.config import get_settings
from app.services.inference_logger import InferencePayload

logger = logging.getLogger(__name__)


_redis_singleton: aioredis.Redis | None = None
_publish_failures = 0  # tracked so we can avoid spamming logs on outage


def _client() -> aioredis.Redis | None:
    """Lazily build a connection. Returns None when REDIS_URL is unset."""
    global _redis_singleton
    settings = get_settings()
    if not settings.redis_url:
        return None
    if _redis_singleton is None:
        _redis_singleton = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_singleton


async def publish(payload: InferencePayload) -> bool:
    """Push one inference event onto the stream. Returns True on success.

    Caller is expected to fall back to the direct DB write path on False.
    """
    global _publish_failures
    client = _client()
    if client is None:
        return False
    settings = get_settings()
    body = asdict(payload)
    body["enqueued_at"] = datetime.now(timezone.utc).isoformat()
    try:
        await client.xadd(
            settings.redis_stream_key,
            {"payload": json.dumps(body, default=str)},
            maxlen=100_000,  # cap the stream so a stuck worker can't fill disk
            approximate=True,
        )
        _publish_failures = 0
        return True
    except Exception as exc:
        _publish_failures += 1
        # Only log every Nth failure to avoid drowning the logs during an outage.
        if _publish_failures <= 3 or _publish_failures % 50 == 0:
            logger.error("Redis publish failed (#%d): %s", _publish_failures, exc)
        return False


async def ensure_consumer_group() -> None:
    """Create the consumer group if it doesn't already exist. Idempotent."""
    client = _client()
    if client is None:
        return
    settings = get_settings()
    try:
        # MKSTREAM creates the stream too if it doesn't exist.
        await client.xgroup_create(
            settings.redis_stream_key,
            settings.redis_consumer_group,
            id="$",
            mkstream=True,
        )
        logger.info(
            "Created consumer group %s on %s",
            settings.redis_consumer_group,
            settings.redis_stream_key,
        )
    except aioredis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def close() -> None:
    global _redis_singleton
    if _redis_singleton is not None:
        try:
            await _redis_singleton.aclose()
        except Exception:
            pass
        _redis_singleton = None
