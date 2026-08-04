"""Taux de change ECB quotidiens via frankfurter.app, avec cache Redis 12h."""
import json
import logging

import httpx

from app.redis_client import get_redis

logger = logging.getLogger(__name__)

BASE_URL = "https://api.frankfurter.app/latest"
CACHE_TTL_SECONDS = 12 * 3600


async def get_rates(base: str) -> dict[str, float]:
    """Taux `rates[X]` = unités de X pour 1 `base`. Cache Redis, fail-open sans cache."""
    base = base.upper()
    cache_key = f"fx:{base}"
    redis = get_redis()
    try:
        cached = await redis.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        logger.warning("Redis indisponible pour le cache FX", exc_info=True)

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        response = await client.get(BASE_URL, params={"base": base})
        response.raise_for_status()
        rates = {k.upper(): float(v) for k, v in response.json().get("rates", {}).items()}

    try:
        await redis.set(cache_key, json.dumps(rates), ex=CACHE_TTL_SECONDS)
    except Exception:
        pass
    return rates


async def convert(amount: float, from_currency: str, to_currency: str) -> float | None:
    """Convertit `amount` de from_currency vers to_currency. None si taux inconnu."""
    from_currency, to_currency = from_currency.upper(), to_currency.upper()
    if from_currency == to_currency:
        return amount
    rates = await get_rates(to_currency)
    rate = rates.get(from_currency)  # unités de from pour 1 to
    if not rate:
        return None
    return amount / rate
