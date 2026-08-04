"""Détection étage 1 (statistique, sur les données Travelpayouts).

Après chaque collecte, examine le meilleur prix de chaque route+devise et lève
une anomalie candidate si au moins un critère est rempli :
- seuil  : prix < (100 - threshold_pct) % de la médiane glissante 30 jours
- record : nouveau minimum absolu sur la route+devise
- chute  : baisse > drop_pct % vs le relevé précédent de moins de drop_window_hours
Le critère cross-devise arrive en Phase 5, la confirmation live Duffel en Phase 4.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.metrics import ANOMALIES_DETECTED
from app.models import PriceSnapshot, Route
from app.schemas import WatchConfig
from app.services.baseline import compute_baseline, store_baseline

logger = logging.getLogger(__name__)

# Ordre = priorité du type principal de l'alerte
CRITERIA_PRIORITY = ["seuil", "cross_devise", "record", "chute"]


@dataclass
class Anomaly:
    route: Route
    currency: str
    price: float
    types: list[str]
    depart_date: object = None
    return_date: object = None
    airline: str | None = None
    transfers: int | None = None
    return_transfers: int | None = None
    link: str | None = None
    fetched_at: datetime | None = None
    median_30d: float | None = None
    baseline_samples: int = 0
    previous_min: float | None = None  # min historique (record) ou relevé précédent (chute)
    details: dict = field(default_factory=dict)

    @property
    def primary_type(self) -> str:
        for t in CRITERIA_PRIORITY:
            if t in self.types:
                return t
        return self.types[0]

    @property
    def pct_below_median(self) -> float | None:
        if self.median_30d and self.median_30d > 0:
            return (1 - self.price / self.median_30d) * 100
        return None


async def _historical_min(
    session: AsyncSession, route_id: int, currency: str, before: datetime
) -> float | None:
    value = await session.scalar(
        select(func.min(PriceSnapshot.price)).where(
            PriceSnapshot.route_id == route_id,
            PriceSnapshot.currency == currency,
            PriceSnapshot.source == "travelpayouts",
            PriceSnapshot.cabin == "economy",
            PriceSnapshot.fetched_at < before,
        )
    )
    return float(value) if value is not None else None


async def _previous_run_min(
    session: AsyncSession, route_id: int, currency: str, before: datetime, window_hours: int
) -> float | None:
    """Meilleur prix de la dernière collecte de moins de `window_hours` heures."""
    prev_fetch = await session.scalar(
        select(func.max(PriceSnapshot.fetched_at)).where(
            PriceSnapshot.route_id == route_id,
            PriceSnapshot.currency == currency,
            PriceSnapshot.source == "travelpayouts",
            PriceSnapshot.fetched_at < before,
            PriceSnapshot.fetched_at >= before - timedelta(hours=window_hours),
        )
    )
    if prev_fetch is None:
        return None
    value = await session.scalar(
        select(func.min(PriceSnapshot.price)).where(
            PriceSnapshot.route_id == route_id,
            PriceSnapshot.currency == currency,
            PriceSnapshot.fetched_at == prev_fetch,
        )
    )
    return float(value) if value is not None else None


async def detect_anomalies(
    session: AsyncSession, config: WatchConfig, run_started_at: datetime
) -> list[Anomaly]:
    """Analyse les relevés de la collecte `run_started_at` et retourne les anomalies candidates."""
    settings = get_settings()

    rows = (
        await session.execute(
            select(PriceSnapshot, Route)
            .join(Route, PriceSnapshot.route_id == Route.id)
            .where(PriceSnapshot.fetched_at == run_started_at)
        )
    ).all()

    # Meilleur prix par (route, devise) sur cette collecte
    best: dict[tuple[int, str], tuple[PriceSnapshot, Route]] = {}
    for snapshot, route in rows:
        key = (snapshot.route_id, snapshot.currency)
        if key not in best or float(snapshot.price) < float(best[key][0].price):
            best[key] = (snapshot, route)

    anomalies_by_key: dict[tuple[int, str], Anomaly] = {}
    stats_map: dict[tuple[int, str], object] = {}
    for (route_id, currency), (snapshot, route) in best.items():
        price = float(snapshot.price)

        stats = await compute_baseline(session, route_id, currency, before=run_started_at)
        await store_baseline(session, route_id, currency, stats)
        stats_map[(route_id, currency)] = stats

        baseline_ready = (
            stats.samples >= settings.min_baseline_samples
            and stats.oldest_at is not None
            and stats.oldest_at
            <= run_started_at - timedelta(hours=settings.min_baseline_age_hours)
        )

        types: list[str] = []
        details: dict = {"baseline_ready": baseline_ready, "samples": stats.samples}
        previous_min: float | None = None

        if baseline_ready and stats.median is not None:
            threshold = stats.median * (1 - config.threshold_pct / 100)
            if price < threshold:
                types.append("seuil")
                details["threshold"] = round(threshold, 2)

        if baseline_ready:
            hist_min = await _historical_min(session, route_id, currency, run_started_at)
            if hist_min is not None and price < hist_min:
                types.append("record")
                previous_min = hist_min

        prev_run_min = await _previous_run_min(
            session, route_id, currency, run_started_at, settings.drop_window_hours
        )
        if prev_run_min is not None and prev_run_min > 0:
            drop = (1 - price / prev_run_min) * 100
            if drop > settings.drop_pct:
                types.append("chute")
                details["drop_pct"] = round(drop, 1)
                if previous_min is None:
                    previous_min = prev_run_min

        if not types:
            continue

        anomalies_by_key[(route_id, currency)] = (
            Anomaly(
                route=route,
                currency=currency,
                price=price,
                types=types,
                depart_date=snapshot.depart_date,
                return_date=snapshot.return_date,
                airline=snapshot.airline,
                transfers=snapshot.transfers,
                return_transfers=snapshot.return_transfers,
                link=snapshot.link,
                fetched_at=snapshot.fetched_at,
                median_30d=stats.median,
                baseline_samples=stats.samples,
                previous_min=previous_min,
                details=details,
            )
        )
        logger.info(
            "Anomalie candidate",
            extra={
                "extra_fields": {
                    "route": f"{route.origin}-{route.destination}",
                    "currency": currency,
                    "price": price,
                    "types": types,
                    "median_30d": stats.median,
                }
            },
        )

    # Critère cross-devise : même route nettement moins chère dans une devise
    # secondaire que dans la devise principale (tarif erroné sur un point de vente)
    if len(config.currencies) > 1:
        try:
            await _cross_currency_pass(config, best, stats_map, anomalies_by_key)
        except Exception:
            logger.warning("Critère cross-devise ignoré (taux indisponibles)", exc_info=True)

    await session.commit()  # persiste les baselines mises à jour
    anomalies = list(anomalies_by_key.values())
    for anomaly in anomalies:
        ANOMALIES_DETECTED.labels(type=anomaly.primary_type).inc()
    return anomalies


async def _cross_currency_pass(
    config: WatchConfig,
    best: dict[tuple[int, str], tuple[PriceSnapshot, Route]],
    stats_map: dict[tuple[int, str], object],
    anomalies_by_key: dict[tuple[int, str], Anomaly],
) -> None:
    from app.services.fx import convert

    settings = get_settings()
    main = config.main_currency
    for (route_id, currency), (snapshot, route) in best.items():
        if currency == main:
            continue
        main_entry = best.get((route_id, main))
        if main_entry is None:
            continue
        main_price = float(main_entry[0].price)
        converted = await convert(float(snapshot.price), currency, main)
        if converted is None or main_price <= 0:
            continue
        gap_pct = (1 - converted / main_price) * 100
        if gap_pct <= settings.cross_currency_pct:
            continue

        cross_details = {
            "converted_main": round(converted, 2),
            "main_price": main_price,
            "gap_pct": round(gap_pct, 1),
            "main_currency": main,
        }
        existing = anomalies_by_key.get((route_id, currency))
        if existing is not None:
            existing.types.append("cross_devise")
            existing.details["cross_devise"] = cross_details
        else:
            stats = stats_map.get((route_id, currency))
            anomalies_by_key[(route_id, currency)] = Anomaly(
                route=route,
                currency=currency,
                price=float(snapshot.price),
                types=["cross_devise"],
                depart_date=snapshot.depart_date,
                return_date=snapshot.return_date,
                airline=snapshot.airline,
                transfers=snapshot.transfers,
                return_transfers=snapshot.return_transfers,
                link=snapshot.link,
                fetched_at=snapshot.fetched_at,
                median_30d=getattr(stats, "median", None),
                baseline_samples=getattr(stats, "samples", 0),
                details={"cross_devise": cross_details},
            )
        logger.info(
            "Anomalie cross-devise",
            extra={
                "extra_fields": {
                    "route": f"{route.origin}-{route.destination}",
                    "currency": currency,
                    **cross_details,
                }
            },
        )
