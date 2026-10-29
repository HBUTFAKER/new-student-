"""Shared FastAPI dependencies."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_principal
from app.db.session import get_db
from app.db.tenant import tenant_context

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


async def get_tenant_db(
    session: DbSession, principal: CurrentPrincipal
) -> AsyncIterator[AsyncSession]:
    """Yield a session whose RLS context is bound to the authenticated tenant."""

    async with tenant_context(session, principal.tenant_id):
        yield session


TenantDbSession = Annotated[AsyncSession, Depends(get_tenant_db)]
