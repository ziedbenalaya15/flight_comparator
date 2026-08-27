"""Tests d'intégration de l'anti-spam (nécessite Redis du compose)."""
from types import SimpleNamespace

from app.redis_client import get_redis
from app.services.alerts import _cooldown_key, _dedup_key, _passes_antispam, _release_antispam
from app.services.detector import Anomaly
from tests.conftest import random_iata


def _anomaly(price=250.0, types=None) -> Anomaly:
    route = SimpleNamespace(id=0, origin=random_iata(), destination=random_iata())
    return Anomaly(route=route, currency="EUR", price=price, types=types or ["seuil"])


async def _cleanup(*anomalies):
    redis = get_redis()
    for a in anomalies:
        await redis.delete(_dedup_key(a), _cooldown_key(a))


async def test_same_alert_never_sent_twice():
    anomaly = _anomaly()
    try:
        assert await _passes_antispam(anomaly) is True
        assert await _passes_antispam(anomaly) is False  # dédupliquée
    finally:
        await _cleanup(anomaly)


async def test_cooldown_blocks_higher_price_but_not_lower():
    first = _anomaly(price=400.0)
    # même route : le cooldown 2h s'applique
    higher = Anomaly(route=first.route, currency="EUR", price=450.0, types=["record"])
    lower = Anomaly(route=first.route, currency="EUR", price=300.0, types=["record"])
    try:
        assert await _passes_antispam(first) is True
        assert await _passes_antispam(higher) is False  # plus cher pendant le cooldown
        assert await _passes_antispam(lower) is True  # encore plus bas : passe
    finally:
        await _cleanup(first, higher, lower)


async def test_dedup_ttl_set():
    anomaly = _anomaly()
    try:
        await _passes_antispam(anomaly)
        ttl = await get_redis().ttl(_dedup_key(anomaly))
        assert 0 < ttl <= 72 * 3600
    finally:
        await _cleanup(anomaly)


async def test_failed_delivery_can_be_retried():
    anomaly = _anomaly()
    try:
        assert await _passes_antispam(anomaly) is True
        await _release_antispam(anomaly)
        assert await _passes_antispam(anomaly) is True
    finally:
        await _cleanup(anomaly)
