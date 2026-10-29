"""Real PostgreSQL RLS tests under the non-superuser app_user role."""

import os
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.db.tenant import tenant_context
from app.models.archive import Student

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run RLS tests",
    ),
]


@dataclass(frozen=True)
class TenantFixtures:
    tenant_a: UUID
    tenant_b: UUID
    student_a: UUID
    student_b: UUID
    sessions: async_sessionmaker[AsyncSession]


@pytest.fixture
async def tenants():
    """Seed through the owner URL; all assertions use the runtime app_user URL."""

    settings = get_settings()
    tenant_a, tenant_b = uuid4(), uuid4()
    student_a, student_b = uuid4(), uuid4()
    owner_engine = create_async_engine(settings.migration_database_url)
    app_engine = create_async_engine(
        settings.database_url,
        pool_size=1,
        max_overflow=0,
        pool_reset_on_return="rollback",
    )
    try:
        async with owner_engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO tenant (id, name) VALUES (:a, 'A'), (:b, 'B')"),
                {"a": tenant_a, "b": tenant_b},
            )
            await connection.execute(
                text(
                    "INSERT INTO student (id, tenant_id, name) VALUES "
                    "(:student_a, :tenant_a, 'A student'), "
                    "(:student_b, :tenant_b, 'B student')"
                ),
                {
                    "student_a": student_a,
                    "student_b": student_b,
                    "tenant_a": tenant_a,
                    "tenant_b": tenant_b,
                },
            )
        yield TenantFixtures(
            tenant_a=tenant_a,
            tenant_b=tenant_b,
            student_a=student_a,
            student_b=student_b,
            sessions=async_sessionmaker(app_engine, expire_on_commit=False),
        )
    finally:
        await app_engine.dispose()
        await owner_engine.dispose()


async def visible_student_ids(session: AsyncSession) -> set[UUID]:
    """Deliberately omit tenant_id: isolation must come from PostgreSQL RLS."""

    return set((await session.scalars(select(Student.id))).all())


async def test_runtime_role_cannot_bypass_rls(tenants: TenantFixtures) -> None:
    """Guard against a false-green suite accidentally using a superuser."""

    async with tenants.sessions() as session:
        role, is_superuser, bypasses_rls = (
            await session.execute(
                text(
                    "SELECT current_user, rolsuper, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()
        await session.rollback()

    assert role == "app_user"
    assert is_superuser is False
    assert bypasses_rls is False


async def test_bidirectional_reads_and_empty_anonymous_context(
    tenants: TenantFixtures,
) -> None:
    """A cannot read B, B cannot read A, and missing context reads nothing."""

    async with tenants.sessions() as session:
        async with tenant_context(session, tenants.tenant_a):
            assert await visible_student_ids(session) == {tenants.student_a}

    async with tenants.sessions() as session:
        async with tenant_context(session, tenants.tenant_b):
            assert await visible_student_ids(session) == {tenants.student_b}

    async with tenants.sessions() as session:
        async with tenant_context(session, None):
            assert await visible_student_ids(session) == set()


async def test_v01_v03_changed_or_guessed_resource_ids_return_empty(
    tenants: TenantFixtures,
) -> None:
    """V-01/V-03: changing URL IDs or enumerating guessed IDs cannot cross RLS."""

    async with tenants.sessions() as session:
        async with tenant_context(session, tenants.tenant_a):
            by_other_id = await session.scalars(
                select(Student).where(Student.id == tenants.student_b)
            )
            guessed_ids = await session.scalars(
                select(Student).where(Student.id.in_([tenants.student_b, uuid4(), uuid4()]))
            )
            assert by_other_id.all() == []
            assert guessed_ids.all() == []


async def test_v02_injected_invalid_identity_has_no_tenant_visibility(
    tenants: TenantFixtures,
) -> None:
    """V-02 skeleton: an unauthenticated/forged principal gets an empty set.

    Real JWT forgery/tampering cases replace this injected stub in step 1.4.
    """

    async with tenants.sessions() as session:
        async with tenant_context(session, None):
            assert await visible_student_ids(session) == set()


async def test_v04_cross_tenant_filter_and_with_check_write_are_rejected(
    tenants: TenantFixtures,
) -> None:
    """V-04: spoofed tenant filters return empty; WITH CHECK rejects writes."""

    async with tenants.sessions() as session:
        async with tenant_context(session, tenants.tenant_a):
            spoofed = await session.scalars(
                select(Student).where(Student.tenant_id == tenants.tenant_b)
            )
            assert spoofed.all() == []

    async with tenants.sessions() as session:
        with pytest.raises(ProgrammingError, match="row-level security|policy"):
            async with tenant_context(session, tenants.tenant_a):
                await session.execute(
                    insert(Student).values(id=uuid4(), tenant_id=tenants.tenant_b, name="intruder")
                )


async def test_write_without_context_is_rejected(tenants: TenantFixtures) -> None:
    """A missing GUC must not turn into an unrestricted write."""

    async with tenants.sessions() as session:
        with pytest.raises(ProgrammingError, match="row-level security|policy"):
            async with tenant_context(session, None):
                await session.execute(
                    insert(Student).values(id=uuid4(), tenant_id=tenants.tenant_a, name="anonymous")
                )


async def test_pooled_connection_does_not_retain_tenant_context(
    tenants: TenantFixtures,
) -> None:
    """A transaction-local GUC is gone after commit and pool reset on return."""

    engine = tenants.sessions.kw["bind"]
    async with engine.connect() as connection:
        await connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenants.tenant_a)},
        )
        assert (await connection.scalars(select(Student.id))).all() == [tenants.student_a]
        await connection.commit()

    # pool_size=1 makes this the same physical connection after reset.
    async with engine.connect() as connection:
        current = await connection.scalar(text("SELECT current_setting('app.tenant_id', true)"))
        assert current in (None, "")  # RESET ALL may represent an unset GUC as ''.
        assert (await connection.scalars(select(Student.id))).all() == []
        await connection.rollback()
