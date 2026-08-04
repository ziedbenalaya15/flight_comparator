from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://flights:flights@localhost:5432/flights"
    redis_url: str = "redis://localhost:6379/0"

    travelpayouts_token: str = ""
    duffel_token: str = ""

    gmail_smtp_user: str = ""
    gmail_app_password: str = ""
    alert_email_to: str = ""

    admin_token: str = "change-me"

    collect_interval_minutes: int = 30
    http_concurrency: int = 5

    # Détection étage 1
    baseline_days: int = 30
    min_baseline_samples: int = 30  # relevés minimum avant d'alerter sur seuil/record
    min_baseline_age_hours: int = 24  # ancienneté minimum de l'historique avant d'alerter
    drop_window_hours: int = 6  # fenêtre de comparaison pour la chute brutale
    drop_pct: float = 50.0  # % de baisse vs relevé précédent pour lever "chute"
    cross_currency_pct: float = 25.0  # % d'écart converti vs devise principale pour "cross_devise"

    # Anti-spam
    dedup_ttl_hours: int = 72
    cooldown_hours: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
