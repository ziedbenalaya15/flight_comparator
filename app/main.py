import logging
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.routes import router as api_router
from app.db import SessionFactory, engine
from app.logging_conf import setup_logging
from app.scheduler import build_scheduler
from app.services.config_service import seed_default_config
from app.web.routes import router as web_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    async with SessionFactory() as session:
        await seed_default_config(session, date.today())
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("Application démarrée, scheduler actif")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await engine.dispose()


app = FastAPI(title="Flight Error Fare Watcher", lifespan=lifespan)
Instrumentator().instrument(app).expose(app, endpoint="/metrics")
app.include_router(api_router)
app.include_router(web_router)
