"""Fixtures communes.

Les tests marqués DB/Redis s'exécutent contre les services du docker-compose :
    docker compose exec app pytest
"""
import random
import string

import pytest
from sqlalchemy import delete, select

from app.db import SessionFactory
from app.models import Alert, PriceSnapshot, Route


def random_iata() -> str:
    return "Q" + "".join(random.choices(string.ascii_uppercase, k=2))


@pytest.fixture
async def db_session():
    async with SessionFactory() as session:
        yield session


@pytest.fixture
async def test_route(db_session):
    """Route jetable avec codes aléatoires, nettoyée en fin de test."""
    origin, destination = random_iata(), random_iata()
    route = Route(origin=origin, destination=destination, active=True)
    db_session.add(route)
    await db_session.commit()
    yield route
    await db_session.execute(delete(Alert).where(Alert.route_id == route.id))
    await db_session.execute(delete(PriceSnapshot).where(PriceSnapshot.route_id == route.id))
    await db_session.execute(delete(Route).where(Route.id == route.id))
    await db_session.commit()
