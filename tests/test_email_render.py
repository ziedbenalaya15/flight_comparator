from datetime import date, datetime, timezone

from app.models import Route
from app.services.alerts import CONFIDENCE_CACHE, CONFIDENCE_LIVE, build_email
from app.services.detector import Anomaly


def _anomaly(**overrides) -> Anomaly:
    defaults = dict(
        route=Route(origin="PAR", destination="ICN"),
        currency="EUR",
        price=312.0,
        types=["seuil", "record"],
        depart_date=date(2026, 10, 5),
        return_date=date(2026, 10, 19),
        airline="KE",
        transfers=1,
        return_transfers=0,
        link="/search/PAR0510ICN1910?t=x",
        fetched_at=datetime.now(timezone.utc),
        median_30d=743.0,
        baseline_samples=54,
        previous_min=520.0,
    )
    defaults.update(overrides)
    return Anomaly(**defaults)


def test_subject_cache_only():
    subject, html, text = build_email(_anomaly(), CONFIDENCE_CACHE)
    assert subject.startswith("📉 Prix bas détecté EUR 312 — PAR → ICN")
    assert "-58 % vs médiane" in subject
    assert "Google Flights" in html
    assert "aviasales.com/search/PAR0510ICN1910" in html
    assert "PAR -> ICN : 312 EUR" in text
    assert "cache" in text.lower()


def test_subject_confirmed_live_with_price():
    subject, html, text = build_email(_anomaly(), CONFIDENCE_LIVE, live_price=329.0)
    assert subject.startswith("🔥 Error fare probable EUR 312")
    assert "329 EUR" in html
    assert "Prix live" in text
    assert "Confirmée live" in html


def test_no_median_no_pct():
    subject, html, text = build_email(
        _anomaly(median_30d=None, baseline_samples=0, types=["chute"]), CONFIDENCE_CACHE
    )
    assert "vs médiane" not in subject
    assert "médiane indisponible" in html


def test_primary_type_priority():
    anomaly = _anomaly(types=["chute", "seuil"])
    assert anomaly.primary_type == "seuil"
    anomaly = _anomaly(types=["chute", "cross_devise"])
    assert anomaly.primary_type == "cross_devise"
