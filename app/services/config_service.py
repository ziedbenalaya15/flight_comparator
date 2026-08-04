"""Lecture/écriture de la configuration de surveillance (table `config`, JSON versionné)."""
import logging
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppConfig
from app.schemas import DateWindow, WatchConfig

logger = logging.getLogger(__name__)


def _next_month_window(today: date, offset_months: int = 1) -> DateWindow:
    month = today.month + offset_months
    year = today.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    start = date(year, month, 1)
    if month == 12:
        first_of_next = date(year + 1, 1, 1)
    else:
        first_of_next = date(year, month + 1, 1)
    return DateWindow(start=start, end=date.fromordinal(first_of_next.toordinal() - 1))


def default_config(today: date) -> WatchConfig:
    return WatchConfig(
        origins=["PAR"],
        destinations=["ICN", "BKK", "JFK"],
        currencies=["EUR"],
        depart_window=_next_month_window(today, offset_months=1),
        return_window=_next_month_window(today, offset_months=2),
        paused=False,
    )


async def get_active_config(session: AsyncSession) -> WatchConfig | None:
    row = await session.scalar(
        select(AppConfig).where(AppConfig.active.is_(True)).order_by(AppConfig.id.desc()).limit(1)
    )
    if row is None:
        return None
    return WatchConfig.model_validate(row.data)


async def save_config(session: AsyncSession, config: WatchConfig) -> None:
    """Versionne : désactive l'ancienne ligne et insère la nouvelle."""
    await session.execute(update(AppConfig).where(AppConfig.active.is_(True)).values(active=False))
    session.add(AppConfig(data=config.model_dump(mode="json"), active=True))
    await session.commit()


async def seed_default_config(session: AsyncSession, today: date) -> WatchConfig:
    existing = await get_active_config(session)
    if existing is not None:
        return existing
    config = default_config(today)
    session.add(AppConfig(data=config.model_dump(mode="json"), active=True))
    await session.commit()
    logger.info("Configuration par défaut créée", extra={"extra_fields": {"config": config.model_dump(mode="json")}})
    return config
