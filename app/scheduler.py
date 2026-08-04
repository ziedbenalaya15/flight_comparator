from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.services.collector import run_collection


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        run_collection,
        "interval",
        minutes=get_settings().collect_interval_minutes,
        id="collect_travelpayouts",
        kwargs={"trigger": "scheduled"},
        coalesce=True,
        max_instances=1,
    )
    return scheduler
