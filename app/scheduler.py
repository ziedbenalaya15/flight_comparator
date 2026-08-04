from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.services.baseline import recalc_all_baselines
from app.services.collector import run_collection
from app.services.digest import send_daily_digest


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
    # La détection recalcule déjà les baselines des routes collectées ; ce job
    # dédié rattrape le reste (routes en pause, redémarrages).
    scheduler.add_job(
        recalc_all_baselines,
        "interval",
        hours=6,
        id="recalc_baselines",
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        send_daily_digest,
        "cron",
        hour=8,
        minute=0,
        timezone="Europe/Paris",
        id="daily_digest",
        coalesce=True,
        max_instances=1,
    )
    return scheduler
