"""Calcul des baselines statistiques par route+devise (médiane 30j, p10, p25, stddev)."""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionFactory
from app.models import Baseline, PriceSnapshot

logger = logging.getLogger(__name__)


@dataclass
class BaselineStats:
    samples: int
    median: float | None
    p10: float | None
    p25: float | None
    stddev: float | None
    oldest_at: datetime | None


async def compute_baseline(
    session: AsyncSession,
    route_id: int,
    currency: str,
    before: datetime | None = None,
) -> BaselineStats:
    """Statistiques sur la fenêtre glissante de 30 jours (source travelpayouts, éco).

    `before` permet d'exclure les relevés de la collecte en cours pour que la
    détection compare le prix du jour à une baseline qui ne le contient pas.
    """
    settings = get_settings()
    now = before or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.baseline_days)
    conditions = [
        PriceSnapshot.route_id == route_id,
        PriceSnapshot.currency == currency,
        PriceSnapshot.source == "travelpayouts",
        PriceSnapshot.cabin == "economy",
        PriceSnapshot.fetched_at >= cutoff,
    ]
    if before is not None:
        conditions.append(PriceSnapshot.fetched_at < before)

    row = (
        await session.execute(
            select(
                func.count(PriceSnapshot.id),
                func.percentile_cont(0.5).within_group(PriceSnapshot.price),
                func.percentile_cont(0.1).within_group(PriceSnapshot.price),
                func.percentile_cont(0.25).within_group(PriceSnapshot.price),
                func.stddev_samp(PriceSnapshot.price),
                func.min(PriceSnapshot.fetched_at),
            ).where(*conditions)
        )
    ).one()

    return BaselineStats(
        samples=row[0] or 0,
        median=float(row[1]) if row[1] is not None else None,
        p10=float(row[2]) if row[2] is not None else None,
        p25=float(row[3]) if row[3] is not None else None,
        stddev=float(row[4]) if row[4] is not None else None,
        oldest_at=row[5],
    )


async def store_baseline(session: AsyncSession, route_id: int, currency: str, stats: BaselineStats) -> None:
    stmt = pg_insert(Baseline).values(
        route_id=route_id,
        currency=currency,
        median_30d=stats.median,
        p10=stats.p10,
        p25=stats.p25,
        stddev=stats.stddev,
        updated_at=func.now(),
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_baselines_route_currency",
        set_={
            "median_30d": stmt.excluded.median_30d,
            "p10": stmt.excluded.p10,
            "p25": stmt.excluded.p25,
            "stddev": stmt.excluded.stddev,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)


async def recalc_all_baselines() -> dict:
    """Job dédié : recalcule les baselines de toutes les paires route+devise vues en DB."""
    async with SessionFactory() as session:
        pairs = (
            await session.execute(
                select(PriceSnapshot.route_id, PriceSnapshot.currency).distinct()
            )
        ).all()
        for route_id, currency in pairs:
            stats = await compute_baseline(session, route_id, currency)
            await store_baseline(session, route_id, currency, stats)
        await session.commit()
    logger.info("Baselines recalculées", extra={"extra_fields": {"pairs": len(pairs)}})
    return {"pairs": len(pairs)}
