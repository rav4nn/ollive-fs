"""Create all tables. Idempotent. Called on app startup as a lightweight
alternative to alembic.

Also applies tiny additive migrations via `ADD COLUMN IF NOT EXISTS` so
in-place upgrades on an existing database don't need manual SQL. Anything more
involved than adding a nullable column should be moved to Alembic.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from app.db.models import Base
from app.db.session import engine

logger = logging.getLogger(__name__)


_ADDITIVE_MIGRATIONS: list[str] = [
    # Added in the streaming PR. Safe to re-run.
    "ALTER TABLE inference_logs ADD COLUMN IF NOT EXISTS time_to_first_token_ms INTEGER",
    # inference_buckets table is created by Base.metadata.create_all so no
    # ALTER needed — leaving this list as the upgrade hook for the next one.
]


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in _ADDITIVE_MIGRATIONS:
            await conn.execute(text(stmt))
    logger.info("Database tables ensured (with %d additive migrations)", len(_ADDITIVE_MIGRATIONS))
