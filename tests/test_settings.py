from app.config import Settings


def test_railway_postgresql_url_uses_asyncpg(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres:secret@postgres.railway.internal:5432/railway",
    )

    settings = Settings(_env_file=None)

    assert settings.database_url == (
        "postgresql+asyncpg://postgres:secret@postgres.railway.internal:5432/railway"
    )


def test_legacy_postgres_url_uses_asyncpg(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgres://postgres:secret@postgres.railway.internal:5432/railway",
    )

    settings = Settings(_env_file=None)

    assert settings.database_url.startswith("postgresql+asyncpg://")


def test_asyncpg_url_is_unchanged(monkeypatch):
    url = "postgresql+asyncpg://flights:flights@db:5432/flights"
    monkeypatch.setenv("DATABASE_URL", url)

    settings = Settings(_env_file=None)

    assert settings.database_url == url
