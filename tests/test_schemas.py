from datetime import date

import pytest

from app.data.country_airports import expand_departure_zone
from app.schemas import WatchConfig, parse_window

WINDOW = {"start": "2026-10-01", "end": "2026-10-14"}


def test_parse_window_month():
    w = parse_window("2026-10")
    assert w.start == date(2026, 10, 1)
    assert w.end == date(2026, 10, 31)


def test_parse_window_month_december():
    w = parse_window("2026-12")
    assert w.end == date(2026, 12, 31)


def test_parse_window_range():
    w = parse_window("2026-10-01 -> 2026-10-14")
    assert (w.start, w.end) == (date(2026, 10, 1), date(2026, 10, 14))


def test_parse_window_invalid():
    with pytest.raises(ValueError):
        parse_window("octobre 2026")


def test_expand_departure_zone_country_and_airport():
    airports = expand_departure_zone(["FR", "BRU", "par"])
    assert "PAR" in airports and "NCE" in airports and "BRU" in airports
    assert airports.count("PAR") == 1  # dédoublonné (FR contient PAR)


def test_config_defaults_and_validators():
    config = WatchConfig(
        destinations=["icn", " bkk "],
        origins=["fr"],
        depart_window=WINDOW,
        currencies=[],
        cabins=["economy", "pilotage"],
        budgets={"economy": 700, "first": -5, "cockpit": 100},
    )
    assert config.destinations == ["ICN", "BKK"]
    assert config.currencies == ["EUR"]  # défaut si vide
    assert config.main_currency == "EUR"
    assert config.cabins == ["economy"]  # cabine inconnue filtrée
    assert config.budgets == {"economy": 700}
    assert "PAR" in config.expanded_origins


def test_destinations_country_expansion():
    config = WatchConfig(destinations=["TH", "ICN"], depart_window=WINDOW)
    assert "BKK" in config.expanded_destinations  # TH développé
    assert "ICN" in config.expanded_destinations  # code précis conservé
    assert config.destinations == ["TH", "ICN"]  # la config stocke la saisie brute


def test_config_stay_nights_order():
    with pytest.raises(ValueError):
        WatchConfig(destinations=["ICN"], depart_window=WINDOW, stay_nights_min=10, stay_nights_max=5)


def test_config_threshold_bounds():
    with pytest.raises(ValueError):
        WatchConfig(destinations=["ICN"], depart_window=WINDOW, threshold_pct=150)
