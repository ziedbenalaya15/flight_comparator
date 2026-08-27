from datetime import date

import pytest

from app.services import duffel


async def test_live_confirmation_converts_currency(monkeypatch):
    offer = duffel.DuffelOffer(
        price=110.0,
        currency="USD",
        cabin="economy",
        airline="XX",
        segments_out=1,
        segments_back=1,
        live_mode=True,
        expires_at="2026-09-01T12:00:00Z",
    )

    async def fake_search(*args, **kwargs):
        return [offer]

    async def fake_convert(amount, from_currency, to_currency):
        assert (amount, from_currency, to_currency) == (110.0, "USD", "EUR")
        return 100.0

    monkeypatch.setattr(duffel, "search_offers", fake_search)
    monkeypatch.setattr(duffel, "convert", fake_convert)

    confirmed, live_price, live_currency = await duffel.confirm_anomaly_live(
        "PAR",
        "BEY",
        date(2026, 9, 1),
        date(2026, 9, 8),
        cached_price=90.0,
        cached_currency="EUR",
        tolerance=1.20,
    )

    assert confirmed is True
    assert live_price == 110.0
    assert live_currency == "USD"


async def test_test_mode_cannot_confirm_a_real_alert(monkeypatch):
    offer = duffel.DuffelOffer(
        price=20.0,
        currency="EUR",
        cabin="economy",
        airline="ZZ",
        segments_out=1,
        segments_back=1,
        live_mode=False,
        expires_at=None,
    )

    async def fake_search(*args, **kwargs):
        return [offer]

    monkeypatch.setattr(duffel, "search_offers", fake_search)

    with pytest.raises(duffel.DuffelUnavailable, match="mode test"):
        await duffel.confirm_anomaly_live(
            "PAR",
            "BEY",
            date(2026, 9, 1),
            date(2026, 9, 8),
            cached_price=100.0,
            cached_currency="EUR",
        )
