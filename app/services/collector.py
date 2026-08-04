"""Job de collecte niveau 1 : Travelpayouts -> price_snapshots.

Interroge l'API par batchs parallèles (semaphore) pour chaque combinaison
origine x destination x devise x mois de départ, filtre sur les fenêtres de
dates configurées et stocke chaque relevé en DB.
"""
import asyncio
import logging
from datetime import date, datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionFactory
from app.metrics import COLLECTION_RUNS, SNAPSHOTS_COLLECTED
from app.models import PriceSnapshot, Route
from app.schemas import DateWindow, WatchConfig
from app.services.config_service import get_active_config
from app.services.travelpayouts import TravelpayoutsClient
from app.state import collection_state

logger = logging.getLogger(__name__)

_run_lock = asyncio.Lock()


def months_in_window(window: DateWindow) -> list[str]:
    """Liste des mois YYYY-MM couverts par une fenêtre de dates."""
    months: list[str] = []
    year, month = window.start.year, window.start.month
    while (year, month) <= (window.end.year, window.end.month):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            month = 1
            year += 1
    return months


def _parse_api_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _in_window(d: date | None, window: DateWindow | None) -> bool:
    if window is None or d is None:
        return True
    return window.start <= d <= window.end


async def _ensure_routes(session: AsyncSession, config: WatchConfig) -> dict[tuple[str, str], Route]:
    """Crée les routes manquantes et retourne toutes les routes configurées, par (origine, destination)."""
    wanted = {(o, d) for o in config.expanded_origins for d in config.destinations if o != d}
    existing = (await session.scalars(select(Route))).all()
    by_key = {(r.origin, r.destination): r for r in existing}
    for origin, destination in sorted(wanted - by_key.keys()):
        route = Route(origin=origin, destination=destination, active=True)
        session.add(route)
        by_key[(origin, destination)] = route
    await session.commit()
    return {key: route for key, route in by_key.items() if key in wanted and route.active}


async def _fetch_one(
    client: TravelpayoutsClient,
    semaphore: asyncio.Semaphore,
    route: Route,
    currency: str,
    month: str,
) -> tuple[Route, str, list[dict]]:
    async with semaphore:
        try:
            offers = await client.prices_for_dates(route.origin, route.destination, currency, month)
        except httpx.HTTPError as exc:
            logger.warning(
                "Échec Travelpayouts",
                extra={
                    "extra_fields": {
                        "origin": route.origin,
                        "destination": route.destination,
                        "currency": currency,
                        "month": month,
                        "error": str(exc),
                    }
                },
            )
            offers = []
        return route, currency, offers


def _offers_to_snapshots(
    route: Route, currency: str, offers: list[dict], config: WatchConfig, fetched_at: datetime
) -> list[PriceSnapshot]:
    snapshots = []
    for offer in offers:
        price = offer.get("price")
        if price is None:
            continue
        depart_date = _parse_api_date(offer.get("departure_at"))
        return_date = _parse_api_date(offer.get("return_at"))
        if not _in_window(depart_date, config.depart_window):
            continue
        if not _in_window(return_date, config.return_window):
            continue
        if depart_date and return_date:
            nights = (return_date - depart_date).days
            if config.stay_nights_min is not None and nights < config.stay_nights_min:
                continue
            if config.stay_nights_max is not None and nights > config.stay_nights_max:
                continue
        if config.max_stopovers is not None:
            transfers = offer.get("transfers")
            return_transfers = offer.get("return_transfers")
            if transfers is not None and transfers > config.max_stopovers:
                continue
            if return_transfers is not None and return_transfers > config.max_stopovers:
                continue
        snapshots.append(
            PriceSnapshot(
                route_id=route.id,
                currency=currency,
                price=price,
                source="travelpayouts",
                cabin="economy",
                transfers=offer.get("transfers"),
                return_transfers=offer.get("return_transfers"),
                airline=offer.get("airline"),
                depart_date=depart_date,
                return_date=return_date,
                link=offer.get("link"),
                fetched_at=fetched_at,
            )
        )
    return snapshots


async def run_collection(trigger: str = "scheduled") -> dict:
    """Exécute une passe de collecte complète. Ignorée si une passe est déjà en cours."""
    if _run_lock.locked():
        return {"status": "skipped", "detail": "collecte déjà en cours"}

    async with _run_lock:
        settings = get_settings()
        collection_state.running = True
        collection_state.last_trigger = trigger
        started_at = datetime.now(timezone.utc)
        try:
            async with SessionFactory() as session:
                config = await get_active_config(session)
                if config is None:
                    raise RuntimeError("aucune configuration active")
                if config.paused:
                    collection_state.last_status = "ok"
                    collection_state.last_detail = "surveillance en pause"
                    collection_state.last_run_at = started_at
                    return {"status": "paused"}
                if not settings.travelpayouts_token:
                    raise RuntimeError("TRAVELPAYOUTS_TOKEN manquant")

                routes = await _ensure_routes(session, config)
                months = months_in_window(config.depart_window)
                tasks = []
                semaphore = asyncio.Semaphore(settings.http_concurrency)
                async with httpx.AsyncClient() as http_client:
                    client = TravelpayoutsClient(settings.travelpayouts_token, http_client)
                    for route in routes.values():
                        for currency in config.currencies:
                            for month in months:
                                tasks.append(_fetch_one(client, semaphore, route, currency, month))
                    results = await asyncio.gather(*tasks)

                total = 0
                for route, currency, offers in results:
                    snapshots = _offers_to_snapshots(route, currency, offers, config, started_at)
                    session.add_all(snapshots)
                    total += len(snapshots)
                await session.commit()

                # Détection étage 1 + alertes : ne doit jamais faire échouer la collecte
                alert_summary: dict = {}
                try:
                    from app.services.alerts import process_anomalies
                    from app.services.detector import detect_anomalies

                    anomalies = await detect_anomalies(session, config, started_at)
                    if anomalies:
                        alert_summary = await process_anomalies(session, anomalies, config)
                except Exception:
                    logger.exception("Échec de la détection/alertes (collecte préservée)")

            collection_state.last_status = "ok"
            collection_state.last_detail = (
                f"{total} relevés sur {len(routes)} routes x {len(config.currencies)} devises x {len(months)} mois"
                + (f" — alertes : {alert_summary}" if alert_summary else "")
            )
            collection_state.last_snapshot_count = total
            SNAPSHOTS_COLLECTED.labels(source="travelpayouts").inc(total)
            COLLECTION_RUNS.labels(status="ok").inc()
            logger.info(
                "Collecte terminée",
                extra={"extra_fields": {"trigger": trigger, "snapshots": total, "routes": len(routes)}},
            )
            return {"status": "ok", "snapshots": total, "routes": len(routes)}
        except Exception as exc:
            collection_state.last_status = "error"
            collection_state.last_detail = str(exc)
            COLLECTION_RUNS.labels(status="error").inc()
            logger.exception("Échec de la collecte")
            return {"status": "error", "detail": str(exc)}
        finally:
            collection_state.running = False
            collection_state.last_run_at = started_at
