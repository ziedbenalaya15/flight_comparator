import pytest

from app.services import fx


@pytest.fixture
def fake_rates(monkeypatch):
    async def _get_rates(base: str):
        assert base == "EUR"
        return {"USD": 1.10, "GBP": 0.85}

    monkeypatch.setattr(fx, "get_rates", _get_rates)


async def test_convert_same_currency():
    assert await fx.convert(100, "EUR", "EUR") == 100


async def test_convert_usd_to_eur(fake_rates):
    result = await fx.convert(110, "USD", "EUR")
    assert result == pytest.approx(100.0)


async def test_convert_unknown_currency(fake_rates):
    assert await fx.convert(100, "XXX", "EUR") is None
