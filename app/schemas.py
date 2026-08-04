from datetime import date

from pydantic import BaseModel, Field, field_validator


class DateWindow(BaseModel):
    start: date
    end: date


class WatchConfig(BaseModel):
    """Configuration de surveillance active (stockée en JSON dans la table `config`)."""

    origins: list[str] = Field(default_factory=lambda: ["PAR"])
    destinations: list[str] = Field(default_factory=list)
    currencies: list[str] = Field(default_factory=lambda: ["EUR"])  # la 1ère = principale
    depart_window: DateWindow
    return_window: DateWindow | None = None
    paused: bool = False

    # Détection & alertes
    threshold_pct: float = 40.0  # % sous la médiane 30j pour lever une anomalie "seuil"
    alert_email_to: str | None = None  # défaut : ALERT_EMAIL_TO de l'environnement
    send_cache_only_alerts: bool = True  # envoyer les alertes non confirmées live (📉)

    @field_validator("origins", "destinations")
    @classmethod
    def _upper_iata(cls, v: list[str]) -> list[str]:
        return [code.strip().upper() for code in v if code.strip()]

    @field_validator("currencies")
    @classmethod
    def _upper_currency(cls, v: list[str]) -> list[str]:
        out = [c.strip().upper() for c in v if c.strip()]
        return out or ["EUR"]
