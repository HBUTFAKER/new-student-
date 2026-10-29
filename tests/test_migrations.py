"""Alembic migration tests against a real PostgreSQL instance.

These tests need ``RUN_DB_TESTS=1`` and a reachable ``DATABASE_URL``; CI provides
both through the PostgreSQL 16 service container. They execute the real Alembic
CLI so the async migration environment is exercised exactly as in production.
"""

import os
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.base import Base
from app.models import BUSINESS_TABLES
from tests.db_support import run_alembic

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run migration tests",
    ),
]


def collect_schema(sync_connection: sa.Connection) -> dict[str, Any]:
    """Read the migrated schema on the sync bridge of an async connection.

    Table-level reflection is skipped when the database is empty, which is the
    expected state right after ``alembic downgrade base``.
    """

    inspector = sa.inspect(sync_connection)
    tables = set(inspector.get_table_names()) - {"alembic_version"}
    snapshot: dict[str, Any] = {"tables": tables}

    if tables:
        snapshot["diffs"] = compare_metadata(
            MigrationContext.configure(sync_connection, opts={"compare_type": True}),
            Base.metadata,
        )
        snapshot["attendance_uniques"] = [
            tuple(sorted(constraint["column_names"]))
            for constraint in inspector.get_unique_constraints("attendance")
        ]
        snapshot["ledger_columns"] = {
            column["name"] for column in inspector.get_columns("lesson_ledger")
        }
        snapshot["business_tenant_columns"] = {
            table_name: "tenant_id"
            in {column["name"] for column in inspector.get_columns(table_name)}
            for table_name in BUSINESS_TABLES
        }

    return snapshot


async def snapshot() -> dict[str, Any]:
    engine = create_async_engine(get_settings().migration_database_url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(collect_schema)
    finally:
        await engine.dispose()


async def test_migration_upgrade_downgrade_upgrade() -> None:
    """Empty database → head → base → head, with schema assertions at each step."""

    run_alembic("upgrade", "head")
    migrated = await snapshot()

    assert len(migrated["tables"]) == 24
    assert migrated["tables"] == set(Base.metadata.tables)
    assert not migrated["diffs"], f"schema drift: {migrated['diffs'][:3]}"
    assert ("lesson_id", "student_id", "tenant_id") in migrated["attendance_uniques"]
    assert "updated_at" not in migrated["ledger_columns"]
    assert all(migrated["business_tenant_columns"].values())

    run_alembic("downgrade", "base")
    rolled_back = await snapshot()

    assert rolled_back["tables"] == set()

    run_alembic("upgrade", "head")
    upgraded_again = await snapshot()

    assert upgraded_again["tables"] == migrated["tables"]
    assert not upgraded_again["diffs"], (
        f"schema drift after re-upgrade: {upgraded_again['diffs'][:3]}"
    )
