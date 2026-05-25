"""Create all tables. Idempotent. Called on app startup as a lightweight alternative to alembic."""

import logging

from app.db.models import Base
from app.db.session import engine

logger = logging.getLogger(__name__)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ensured")
