import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router
from app.config import get_settings
from app.db.init_db import init_db
from app.services.event_bus import close as close_event_bus
from app.workers.ingestion import run_worker
from app.workers.log_consumer import run_consumer

import os

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    stop_event = asyncio.Event()
    tasks = [
        asyncio.create_task(run_worker(stop_event), name="aggregation-worker"),
        asyncio.create_task(run_consumer(stop_event), name="log-consumer"),
    ]
    try:
        yield
    finally:
        stop_event.set()
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await close_event_bus()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="ollive backend", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)
    return app


app = create_app()
