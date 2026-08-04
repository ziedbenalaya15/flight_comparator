"""Niveau 2 — Duffel API (offres temps réel), module isolé.

Utilisé uniquement :
- pour confirmer en live une anomalie candidate détectée par l'étage 1 ;
- à la demande via le bouton « Prix cabines avant » (/api/premium).
Jamais en balayage systématique (préservation des quotas).

Circuit breaker : après `_FAILURE_THRESHOLD` échecs consécutifs (401/403,
timeouts, 5xx), le circuit s'ouvre pendant `_OPEN_SECONDS` et toutes les
requêtes sont court-circuitées — le suivi principal continue sans Duffel.
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.duffel.com"
_FAILURE_THRESHOLD = 3
_OPEN_SECONDS = 600


class CircuitBreaker:
    def __init__(self) -> None:
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if time.monotonic() - self.opened_at >= _OPEN_SECONDS:
            # half-open : on retente une requête
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= _FAILURE_THRESHOLD and self.opened_at is None:
            self.opened_at = time.monotonic()
            logger.warning(
                "Circuit breaker Duffel OUVERT",
                extra={"extra_fields": {"failures": self.failures, "open_seconds": _OPEN_SECONDS}},
            )
        elif self.opened_at is not None:
            self.opened_at = time.monotonic()  # échec en half-open : on repart pour un cycle


_breaker = CircuitBreaker()


@dataclass
class DuffelOffer:
    price: float
    currency: str
    cabin: str
    airline: str | None
    segments_out: int
    segments_back: int


class DuffelUnavailable(Exception):
    """Duffel désactivé (pas de token), circuit ouvert ou échec de la requête."""


def duffel_enabled() -> bool:
    return bool(get_settings().duffel_token)


async def search_offers(
    origin: str,
    destination: str,
    depart_date: date,
    return_date: date | None,
    cabin: str = "economy",
    max_offers: int = 5,
) -> list[DuffelOffer]:
    """Crée une offer request Duffel et retourne les offres les moins chères."""
    settings = get_settings()
    if not settings.duffel_token:
        raise DuffelUnavailable("DUFFEL_TOKEN non configuré")
    if _breaker.is_open:
        raise DuffelUnavailable("circuit breaker ouvert")

    slices = [{"origin": origin, "destination": destination, "departure_date": depart_date.isoformat()}]
    if return_date is not None:
        slices.append({"origin": destination, "destination": origin, "departure_date": return_date.isoformat()})

    payload = {
        "data": {
            "slices": slices,
            "passengers": [{"type": "adult"}],
            "cabin_class": cabin,
        }
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0)) as client:
            response = await client.post(
                f"{BASE_URL}/air/offer_requests",
                params={"return_offers": "true"},
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.duffel_token}",
                    "Duffel-Version": "v2",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
    except (httpx.TransportError, httpx.HTTPStatusError) as exc:
        _breaker.record_failure()
        logger.warning(
            "Échec Duffel",
            extra={
                "extra_fields": {
                    "route": f"{origin}-{destination}",
                    "error": str(exc),
                    "failures": _breaker.failures,
                }
            },
        )
        raise DuffelUnavailable(str(exc)) from exc

    _breaker.record_success()
    offers_raw = response.json().get("data", {}).get("offers", [])
    offers = []
    for raw in offers_raw:
        try:
            slices_raw = raw.get("slices", [])
            offers.append(
                DuffelOffer(
                    price=float(raw["total_amount"]),
                    currency=raw["total_currency"],
                    cabin=cabin,
                    airline=(raw.get("owner") or {}).get("iata_code"),
                    segments_out=len(slices_raw[0].get("segments", [])) if slices_raw else 0,
                    segments_back=len(slices_raw[1].get("segments", [])) if len(slices_raw) > 1 else 0,
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    offers.sort(key=lambda o: o.price)
    return offers[:max_offers]


async def confirm_anomaly_live(
    origin: str,
    destination: str,
    depart_date: date | None,
    return_date: date | None,
    cached_price: float,
    tolerance: float = 1.20,
) -> tuple[bool, float | None]:
    """Vérifie qu'une offre live existe à un prix proche du prix cache détecté.

    Retourne (confirmé, meilleur prix live). Confirmé si le meilleur prix live
    est <= prix cache x tolérance (20 % de marge : les prix cache ont ~48h).
    """
    if depart_date is None:
        raise DuffelUnavailable("date de départ inconnue, confirmation impossible")
    offers = await search_offers(origin, destination, depart_date, return_date, cabin="economy")
    if not offers:
        return False, None
    best = offers[0].price
    return best <= cached_price * tolerance, best
