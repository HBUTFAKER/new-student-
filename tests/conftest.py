"""Shared test setup: migrate a fresh CI database before any app connection."""

import os

import pytest

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
