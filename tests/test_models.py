"""Static checks that the ORM models match docs/phase0/04-data-dictionary.md."""

import re

import pytest
from sqlalchemy import UniqueConstraint, Uuid

from app.db.base import Base
from app.models import BUSINESS_TABLES, PLATFORM_TABLES, RLS_TABLES, WECHAT_TABLES

ALL_TABLES = tuple(sorted(Base.metadata.tables))


def test_table_layers_match_dictionary() -> None:
    assert len(PLATFORM_TABLES) == 4
    assert len(WECHAT_TABLES) == 3
    assert len(BUSINESS_TABLES) == 17
    assert len(set(BUSINESS_TABLES)) == 17
    assert set(Base.metadata.tables) == {*PLATFORM_TABLES, *WECHAT_TABLES, *BUSINESS_TABLES}


@pytest.mark.parametrize("table_name", BUSINESS_TABLES)
def test_business_tables_are_tenant_scoped(table_name: str) -> None:
    """Red line 1: every business table carries a NOT NULL tenant_id FK."""

    column = Base.metadata.tables[table_name].c.tenant_id

    assert column.nullable is False
    assert {fk.target_fullname for fk in column.foreign_keys} == {"tenant.id"}


@pytest.mark.parametrize("table_name", BUSINESS_TABLES)
def test_business_table_indexes_start_with_tenant_id(table_name: str) -> None:
    for index in Base.metadata.tables[table_name].indexes:
        first_column = next(iter(index.columns))
        assert first_column.name == "tenant_id", f"{index.name} must start with tenant_id"


@pytest.mark.parametrize("table_name", ["wechat_user", "wechat_openid"])
def test_platform_wide_wechat_identity_has_no_tenant_id(table_name: str) -> None:
    """0.6 decision 2: one WeChat person may serve several tenants."""

    assert "tenant_id" not in Base.metadata.tables[table_name].c


@pytest.mark.parametrize("table_name", ALL_TABLES)
def test_primary_key_is_database_generated_uuid(table_name: str) -> None:
    table = Base.metadata.tables[table_name]
    primary_key = list(table.primary_key.columns)

    assert [column.name for column in primary_key] == ["id"]
    assert isinstance(primary_key[0].type, Uuid)
    assert primary_key[0].server_default.arg.text == "gen_random_uuid()"


@pytest.mark.parametrize("table_name", ALL_TABLES)
def test_created_at_defaults_to_now(table_name: str) -> None:
    column = Base.metadata.tables[table_name].c.created_at

    assert column.nullable is False
    assert column.server_default.arg.text == "now()"


@pytest.mark.parametrize("table_name", ALL_TABLES)
def test_table_names_are_snake_case(table_name: str) -> None:
    assert re.fullmatch(r"[a-z][a-z0-9_]*", table_name)
    # ``class`` is the only table whose name legitimately ends with "s".
    assert table_name == "class" or not table_name.endswith("s")


def test_ledger_is_append_only() -> None:
    """Red line 2: no mutable balance and no updated_at on lesson_ledger."""

    table = Base.metadata.tables["lesson_ledger"]
    columns = set(table.c.keys())

    assert "updated_at" not in columns
    assert not {"balance", "remaining", "balance_after"} & columns
    assert "created_at" in columns


def test_ledger_consume_columns_are_physically_nullable() -> None:
    table = Base.metadata.tables["lesson_ledger"]

    # "consume 必填" is enforced by the service layer, not a CHECK constraint.
    assert table.c.purchase_id.nullable is True
    assert table.c.lesson_id.nullable is True
    assert {fk.target_fullname for fk in table.c.purchase_id.foreign_keys} == {"purchase.id"}
    assert {fk.target_fullname for fk in table.c.lesson_id.foreign_keys} == {"lesson.id"}


def test_attendance_unique_key_is_the_idempotency_key() -> None:
    """Red line 3: (tenant_id, lesson_id, student_id) is unique."""

    table = Base.metadata.tables["attendance"]
    unique_column_sets = {
        tuple(sorted(column.name for column in constraint.columns))
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("lesson_id", "student_id", "tenant_id") in unique_column_sets


def test_no_table_has_updated_at() -> None:
    """The dictionary defines no updated_at column; nothing may add one silently."""

    for table_name in ALL_TABLES:
        assert "updated_at" not in Base.metadata.tables[table_name].c, table_name


def test_rls_covers_every_tenant_scoped_table() -> None:
    assert set(BUSINESS_TABLES).issubset(RLS_TABLES)
    assert "tenant" not in RLS_TABLES
    assert "plan" not in RLS_TABLES
