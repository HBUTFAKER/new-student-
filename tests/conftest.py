"""Shared test setup: migrate a fresh CI database and provide DB/HTTP clients."""

import os
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import get_settings
from app.db.engine import engine as app_engine
from app.main import create_app
from tests.db_support import run_alembic


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    """Create tables and app_user before tests that open runtime connections.

    CI starts from an empty database. The ``/health/db`` test connects with the
    runtime role, so the schema and role must exist even before the dedicated
    migration test runs.
    """

    if os.getenv("RUN_DB_TESTS") == "1":
        run_alembic("upgrade", "head")


@pytest.fixture
async def owner_engine() -> AsyncIterator[AsyncEngine]:
    """Owner/superuser connection used only to seed rows across tenants."""

    engine = create_async_engine(get_settings().migration_database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """ASGI client; disposes the global pool because each test has its own loop."""

    application = create_app()
    transport = httpx.ASGITransport(app=application)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http
    finally:
        await app_engine.dispose()
