"""Shared FastAPI dependencies: DB session, principal, tenant context."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal
from app.core.errors import AppError
from app.core.security import TokenExpiredError, TokenInvalidError, decode_token
from app.db.session import get_db
from app.db.tenant import tenant_context
from app.services.auth import resolve_principal

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppError(
            401,
            "TOKEN_REQUIRED",
            "请先登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[7:].strip()
    if not token:
        raise AppError(401, "TOKEN_REQUIRED", "请先登录")
    return token


async def get_principal(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Verify the JWT signature, then re-read role/tenant from PostgreSQL.

    A transaction opened by these lookups is rolled back before returning, so
    downstream tenant dependencies always start from a clean session.
    """

    token = _bearer_token(authorization)
    try:
        claims = decode_token(token, expected_type="access")
    except TokenExpiredError as exc:
        raise AppError(
            401,
            "TOKEN_EXPIRED",
            "登录已过期",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except TokenInvalidError as exc:
        raise AppError(
            401,
            "TOKEN_INVALID",
            "登录状态无效",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    principal = await resolve_principal(db, claims)
    await db.rollback()
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


async def get_tenant_db(
    session: DbSession, principal: CurrentPrincipal
) -> AsyncIterator[AsyncSession]:
    """Bind the transaction to the authenticated tenant; reject tenantless parents."""

    if not principal.has_tenant:
        raise AppError(403, "TENANT_CONTEXT_REQUIRED", "请先选择孩子")
    async with tenant_context(session, principal.tenant_id):
        yield session


TenantDbSession = Annotated[AsyncSession, Depends(get_tenant_db)]
