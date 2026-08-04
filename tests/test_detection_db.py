"""Tests d'intégration de la détection étage 1 (nécessite Postgres du compose)."""
import random
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models import PriceSnapshot
from app.schemas import WatchConfig
from app.services import fx
from app.services.detector import detect_anomalies

random.seed(1234)

CONFIG = WatchConfig(
    destinations=["ICN"],
    origins=["PAR"],
    depart_window={"start": "2026-09-01", "end": "2026-09-30"},
    currencies=["EUR", "USD"],
    threshold_pct=40.0,
)


def _snapshot(route, price, currency="EUR", fetched_at=None, **kw):
    return PriceSnapshot(
        route_id=route.id,
        currency=currency,
        price=price,
        source="travelpayouts",
        cabin="economy",
        transfers=1,
        airline="KE",
        depart_date=date(2026, 9, 10),
        return_date=date(2026, 10, 5),
        fetched_at=fetched_at,
        **kw,
    )


async def _seed_history(session, route, days=28, per_day=2, low=550, high=680, currency="EUR"):
    now = datetime.now(timezone.utc)
    for day in range(days, 0, -1):
        for _ in range(per_day):
            session.add(
                _snapshot(route, round(random.uniform(low, high), 2), currency,
                          fetched_at=now - timedelta(days=day))
            )
    await session.commit()
    return now


async def test_threshold_record_and_drop(db_session, test_route):
    now = await _seed_history(db_session, test_route)
    # relevé précédent récent (30 min) pour le critère chute
    db_session.add(_snapshot(test_route, 610, fetched_at=now - timedelta(minutes=30)))
    # collecte courante : 250 EUR, ~59 % sous la médiane
    db_session.add(_snapshot(test_route, 250, fetched_at=now))
    await db_session.commit()

    anomalies = await detect_anomalies(db_session, CONFIG, now)
    assert len(anomalies) == 1
    anomaly = anomalies[0]
    assert set(anomaly.types) == {"seuil", "record", "chute"}
    assert anomaly.primary_type == "seuil"
    assert anomaly.median_30d == pytest.approx(610, abs=30)
    assert anomaly.pct_below_median > 50


async def test_normal_price_no_anomaly(db_session, test_route):
    now = await _seed_history(db_session, test_route)
    db_session.add(_snapshot(test_route, 590, fetched_at=now))  # dans la norme
    await db_session.commit()
    anomalies = await detect_anomalies(db_session, CONFIG, now)
    assert anomalies == []


async def test_no_alert_without_enough_history(db_session, test_route):
    """Sans 30 relevés et 24h d'historique, seuil/record sont muets (anti-bruit jour 1)."""
    now = datetime.now(timezone.utc)
    db_session.add(_snapshot(test_route, 600, fetched_at=now - timedelta(hours=2)))
    db_session.add(_snapshot(test_route, 200, fetched_at=now))
    await db_session.commit()
    anomalies = await detect_anomalies(db_session, CONFIG, now)
    # la chute (>50 % vs relevé < 6h) reste active, mais ni seuil ni record
    for anomaly in anomalies:
        assert "seuil" not in anomaly.types
        assert "record" not in anomaly.types


async def test_cross_currency(db_session, test_route, monkeypatch):
    async def fake_rates(base):
        assert base == "EUR"
        return {"USD": 1.10}

    monkeypatch.setattr(fx, "get_rates", fake_rates)

    now = datetime.now(timezone.utc)
    # même route : 600 EUR vs 300 USD (~273 EUR convertis, écart ~54 % > 25 %)
    db_session.add(_snapshot(test_route, 600, "EUR", fetched_at=now))
    db_session.add(_snapshot(test_route, 300, "USD", fetched_at=now))
    await db_session.commit()

    anomalies = await detect_anomalies(db_session, CONFIG, now)
    cross = [a for a in anomalies if "cross_devise" in a.types]
    assert len(cross) == 1
    assert cross[0].currency == "USD"
    assert cross[0].details["cross_devise"]["gap_pct"] > 50
