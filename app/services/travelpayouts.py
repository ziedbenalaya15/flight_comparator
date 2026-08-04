"""Client Travelpayouts / Aviasales Data API (niveau 1 — baseline, données cache ~48h)."""
import logging

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return False


class TravelpayoutsClient:
    def __init__(self, token: str, client: httpx.AsyncClient) -> None:
        self._token = token
        self._client = client

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def prices_for_dates(
        self,
        origin: str,
        destination: str,
        currency: str,
        departure_at: str,  # YYYY-MM ou YYYY-MM-DD
        one_way: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """Retourne les offres (aller-retour par défaut) pour un mois/jour de départ donné."""
        params = {
            "origin": origin,
            "destination": destination,
            "currency": currency.lower(),
            "departure_at": departure_at,
            "one_way": "true" if one_way else "false",
            "unique": "false",
            "sorting": "price",
            "direct": "false",
            "limit": limit,
            "page": 1,
        }
        response = await self._client.get(
            BASE_URL,
            params=params,
            headers={"X-Access-Token": self._token},
            timeout=httpx.Timeout(20.0),
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("success", False):
            logger.warning(
                "Réponse Travelpayouts non-success",
                extra={"extra_fields": {"origin": origin, "destination": destination, "body": body}},
            )
            return []
        return body.get("data", [])
