"""Transaction-scoped tenant context for PostgreSQL row-level security."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def tenant_context(
    session: AsyncSession, tenant_id: UUID | None
) -> AsyncIterator[AsyncSession]:
    """Run one request in one transaction with a transaction-local tenant GUC.

    ``set_config(..., true)`` is used instead of ``SET`` because the third
    argument makes the value transaction-local. Commit or rollback therefore
    clears it automatically. SQLAlchemy's pool also rolls back on return
    (``pool_reset_on_return='rollback'``), so a reused connection starts clean.

    This is preferred over ``SET SESSION`` (can leak across pooled requests)
    and ``DISCARD ALL`` (also drops prepared statements and other useful session
    state). No tenant means no usable GUC: RLS treats NULL/empty as no tenant
    and returns an empty set (the policy uses NULLIF before the UUID cast).
    """

    async with session.begin():
        if tenant_id is not None:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
        yield session
