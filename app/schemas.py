import re
from datetime import date

from pydantic import BaseModel, Field, field_validator, model_validator

from app.data.country_airports import expand_departure_zone

CABINS = ["economy", "premium_economy", "business", "first"]
CABIN_LABELS = {
    "economy": "Économique",
    "premium_economy": "Éco premium",
    "business": "Affaires",
    "first": "First",
}

CURRENCY_ALIASES = {
    "DOL": "USD",
    "DOLLAR": "USD",
    "DOLLARS": "USD",
    "EURO": "EUR",
    "EUROS": "EUR",
}


class DateWindow(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> "DateWindow":
        if self.end < self.start:
            raise ValueError("la fin de la fenêtre doit être après le début")
        return self


def parse_window(value: str) -> DateWindow:
    """Accepte une plage `2026-10-01 -> 2026-10-14` (ou `/`) ou un mois entier `2026-10`."""
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}", value):
        year, month = int(value[:4]), int(value[5:7])
        start = date(year, month, 1)
        first_of_next = date(year + (month == 12), month % 12 + 1, 1)
        return DateWindow(start=start, end=date.fromordinal(first_of_next.toordinal() - 1))
    parts = re.split(r"\s*(?:->|→|/|à)\s*", value)
    if len(parts) == 2:
        return DateWindow(start=date.fromisoformat(parts[0]), end=date.fromisoformat(parts[1]))
    raise ValueError(f"fenêtre de dates invalide : {value!r} (attendu YYYY-MM ou YYYY-MM-DD -> YYYY-MM-DD)")


class WatchConfig(BaseModel):
    """Configuration de surveillance active (stockée en JSON dans la table `config`).

    Les 8 critères de l'onboarding + devises, seuil, email, pause.
    """

    # 1. Destinations : aéroports/villes IATA (3 lettres) ou pays ISO (2 lettres, développés)
    destinations: list[str] = Field(default_factory=list)
    # 2. Zone de départ : aéroports IATA (3 lettres) ou pays ISO (2 lettres, développés)
    origins: list[str] = Field(default_factory=lambda: ["PAR"])
    # 3-4. Fenêtres de dates
    depart_window: DateWindow
    return_window: DateWindow | None = None
    # 5. Durée du séjour (nuits)
    stay_nights_min: int | None = None
    stay_nights_max: int | None = None
    # 6. Cabines suivies (éco via Travelpayouts ; les autres via Duffel, Phase 4)
    cabins: list[str] = Field(default_factory=lambda: ["economy"])
    # 7. Escales max par trajet (None = libre)
    max_stopovers: int | None = None
    # 8. Budget repère par cabine, dans la devise principale (au-dessus : marquage ⚠️)
    budgets: dict[str, float] = Field(default_factory=dict)

    # Devises suivies (la 1ère = principale)
    currencies: list[str] = Field(default_factory=lambda: ["EUR"])
    # Détection & alertes
    threshold_pct: float = Field(default=40.0, gt=0, lt=100)
    alert_email_to: str | None = None  # défaut : ALERT_EMAIL_TO de l'environnement
    send_cache_only_alerts: bool = True
    digest_enabled: bool = False  # digest quotidien 8h (Phase 5)
    paused: bool = False

    @field_validator("origins", "destinations")
    @classmethod
    def _upper_iata(cls, v: list[str]) -> list[str]:
        return [code.strip().upper() for code in v if code.strip()]

    @field_validator("currencies")
    @classmethod
    def _upper_currency(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for raw in v:
            code = raw.strip().upper()
            if not code:
                continue
            code = CURRENCY_ALIASES.get(code, code)
            if not re.fullmatch(r"[A-Z]{3}", code):
                raise ValueError(f"devise invalide : {raw!r} (attendu EUR, USD, GBP…)")
            if code not in out:
                out.append(code)
        return out or ["EUR"]

    @field_validator("cabins")
    @classmethod
    def _known_cabins(cls, v: list[str]) -> list[str]:
        out = [c for c in v if c in CABINS]
        return out or ["economy"]

    @field_validator("budgets")
    @classmethod
    def _budget_cabins(cls, v: dict[str, float]) -> dict[str, float]:
        return {cabin: amount for cabin, amount in v.items() if cabin in CABINS and amount > 0}

    @model_validator(mode="after")
    def _stay_ordered(self) -> "WatchConfig":
        if (
            self.stay_nights_min is not None
            and self.stay_nights_max is not None
            and self.stay_nights_max < self.stay_nights_min
        ):
            raise ValueError("stay_nights_max doit être >= stay_nights_min")
        return self

    @property
    def main_currency(self) -> str:
        return self.currencies[0]

    @property
    def expanded_origins(self) -> list[str]:
        return expand_departure_zone(self.origins)

    @property
    def expanded_destinations(self) -> list[str]:
        return expand_departure_zone(self.destinations)
