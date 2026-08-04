"""Tests d'intégration des endpoints /api/config (nécessite Postgres du compose)."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.main import app
from app.services.config_service import get_active_config, save_config

NEW_CONFIG = {
    "destinations": ["ICN", "BKK"],
    "origins": ["FR"],
    "depart_window": {"start": "2026-11-01", "end": "2026-11-20"},
    "return_window": {"start": "2026-11-10", "end": "2026-12-05"},
    "stay_nights_min": 7,
    "stay_nights_max": 21,
    "cabins": ["economy", "business"],
    "max_stopovers": 1,
    "budgets": {"economy": 650},
    "currencies": ["EUR", "USD"],
    "threshold_pct": 35,
}


@pytest.fixture
async def client(db_session):
    # sauvegarde la config active pour la restaurer après le test
    previous = await get_active_config(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    if previous is not None:
        await save_config(db_session, previous)


@pytest.fixture
def auth():
    return {"X-Admin-Token": get_settings().admin_token}


async def test_config_requires_auth(client):
    response = await client.get("/api/config")
    assert response.status_code == 401


async def test_put_then_get_roundtrip(client, auth):
    response = await client.put("/api/config", json=NEW_CONFIG, headers=auth)
    assert response.status_code == 200
    assert "PAR" in response.json()["expanded_origins"]

    response = await client.get("/api/config", headers=auth)
    assert response.status_code == 200
    config = response.json()["config"]
    assert config["destinations"] == ["ICN", "BKK"]
    assert config["threshold_pct"] == 35
    assert config["budgets"] == {"economy": 650.0}
    assert config["currencies"] == ["EUR", "USD"]


async def test_put_invalid_config_rejected(client, auth):
    bad = dict(NEW_CONFIG, threshold_pct=150)
    response = await client.put("/api/config", json=bad, headers=auth)
    assert response.status_code == 422


async def test_pause_resume(client, auth):
    response = await client.post("/api/config/pause", headers=auth)
    assert response.json()["paused"] is True
    response = await client.get("/api/config", headers=auth)
    assert response.json()["config"]["paused"] is True
    response = await client.post("/api/config/resume", headers=auth)
    assert response.json()["paused"] is False
