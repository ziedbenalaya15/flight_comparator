"""Interrogation Duffel à la demande pour les cabines avant (équivalent /premium).

Module isolé : un échec Duffel n'affecte jamais le suivi éco principal.
Les résultats sont stockés en snapshots (source=duffel) et retournés à l'UI.
"""
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PriceSnapshot, Route
from app.schemas import CABIN_LABELS, WatchConfig
from app.services.duffel import DuffelUnavailable, search_offers

logger = logging.getLogger(__name__)

MAX_ROUTES_PER_CHECK = 5  # préservation du quota Duffel


def _pick_dates(config: WatchConfig) -> tuple[date, date | None]:
    """Dates représentatives : milieu de la fenêtre aller, retour cohérent."""
    dw = config.depart_window
    depart = dw.start + (dw.end - dw.start) / 2
    if config.return_window is not None:
        rw = config.return_window
        ret = rw.start + (rw.end - rw.start) / 2
        if ret <= depart:
            ret = depart + timedelta(days=config.stay_nights_min or 7)
    elif config.stay_nights_min is not None:
        ret = depart + timedelta(days=config.stay_nights_min)
    else:
        ret = depart + timedelta(days=7)
    return depart, ret


async def premium_check(session: AsyncSession, config: WatchConfig) -> dict:
    premium_cabins = [c for c in config.cabins if c != "economy"]
    if not premium_cabins:
        return {"status": "no_premium_cabins", "detail": "aucune cabine avant dans la config", "results": []}

    pairs = {(o, d) for o in config.expanded_origins for d in config.destinations if o != d}
    routes = (
        (
            await session.execute(
                select(Route).where(Route.active.is_(True)).order_by(Route.id)
            )
        )
        .scalars()
        .all()
    )
    routes = [r for r in routes if (r.origin, r.destination) in pairs][:MAX_ROUTES_PER_CHECK]
    depart, ret = _pick_dates(config)

    results = []
    fetched_at = datetime.now(timezone.utc)
    for route in routes:
        for cabin in premium_cabins:
            entry = {
                "origin": route.origin,
                "destination": route.destination,
                "cabin": cabin,
                "cabin_label": CABIN_LABELS.get(cabin, cabin),
                "depart_date": depart.isoformat(),
                "return_date": ret.isoformat() if ret else None,
            }
            try:
                offers = await search_offers(route.origin, route.destination, depart, ret, cabin=cabin)
            except DuffelUnavailable as exc:
                entry.update({"status": "unavailable", "detail": str(exc)})
                results.append(entry)
                continue
            budget = config.budgets.get(cabin)
            entry["status"] = "ok"
            entry["offers"] = [
                {
                    "price": offer.price,
                    "currency": offer.currency,
                    "airline": offer.airline,
                    "segments_out": offer.segments_out,
                    "segments_back": offer.segments_back,
                    "over_budget": bool(budget and offer.price > budget),
                }
                for offer in offers
            ]
            for offer in offers:
                session.add(
                    PriceSnapshot(
                        route_id=route.id,
                        currency=offer.currency,
                        price=offer.price,
                        source="duffel",
                        cabin=cabin,
                        transfers=max(offer.segments_out - 1, 0),
                        return_transfers=max(offer.segments_back - 1, 0) if ret else None,
                        airline=offer.airline,
                        depart_date=depart,
                        return_date=ret,
                        fetched_at=fetched_at,
                    )
                )
            results.append(entry)
    await session.commit()
    return {"status": "ok", "results": results}
