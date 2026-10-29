"""Health endpoint tests."""

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_db
from app.main import app


class FakeSession:
    """Minimal AsyncSession stand-in for route tests."""

    async def execute(self, *_args, **_kwargs) -> None:
        return None


class FailingSession(FakeSession):
    """Session stub that simulates an unavailable database."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def execute(self, *_args, **_kwargs) -> None:
        raise self.error


async def _get_json(path: str) -> tuple[int, dict[str, str]]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)
    return response.status_code, response.json()


async def test_health_returns_ok() -> None:
    status_code, body = await _get_json("/health")

    assert status_code == 200
    assert body == {"status": "ok"}


async def test_health_db_returns_ok_with_working_database() -> None:
    async def override_db():
        yield FakeSession()

    app.dependency_overrides[get_db] = override_db
    try:
        status_code, body = await _get_json("/health/db")
    finally:
        app.dependency_overrides.clear()

    assert status_code == 200
    assert body == {"status": "ok", "database": "ok"}


@pytest.mark.parametrize(
    "error",
    [SQLAlchemyError("database unavailable"), ConnectionRefusedError("db refused")],
    ids=["sqlalchemy-error", "connection-refused"],
)
async def test_health_db_returns_503_when_database_fails(error: Exception) -> None:
    async def override_db():
        yield FailingSession(error)

    app.dependency_overrides[get_db] = override_db
    try:
        status_code, body = await _get_json("/health/db")
    finally:
        app.dependency_overrides.clear()

    assert status_code == 503
    assert body == {"status": "error", "database": "unavailable"}


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="set RUN_DB_TESTS=1 and start PostgreSQL to run integration tests",
)
async def test_health_db_against_real_postgres() -> None:
    status_code, body = await _get_json("/health/db")

    assert status_code == 200
    assert body == {"status": "ok", "database": "ok"}
