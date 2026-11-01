"""Staff account administration endpoints (US-301): create/revive, list, update, self-deactivate."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import CurrentPrincipal, DbSession, TenantDbSession
from app.api.permissions import require_role
from app.core.auth import Principal
from app.core.errors import AppError
from app.schemas.staff import (
    SelfDeactivateRequest,
    StaffCreate,
    StaffCreateResponse,
    StaffList,
    StaffOut,
    StaffUpdate,
)
from app.services.staff_admin import (
    create_staff_account,
    list_staff_accounts,
    self_deactivate,
    update_staff_account,
)

router = APIRouter(prefix="/staff", tags=["staff"])
account_router = APIRouter(prefix="/me", tags=["staff"])

AdminOnly = Annotated[Principal, Depends(require_role("admin"))]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


def _changes(payload: StaffUpdate) -> dict:
    """Partial-update body with NOT NULL guards; explicit ``teacher_id: null`` clears."""

    data = payload.model_dump(exclude_unset=True)
    for field in ("name", "role", "status"):
        if field in data and data[field] is None:
            raise AppError(422, "VALIDATION_ERROR", f"{field} 不能为空")
    if "password" in data and data["password"] is None:
        raise AppError(422, "VALIDATION_ERROR", "password 不能为空")
    return data


@router.post(
    "",
    response_model=StaffCreateResponse,
    summary="创建员工账号（同手机号停用账号自动复活）",
)
async def create_staff(
    payload: StaffCreate,
    db: TenantDbSession,
    principal: AdminOnly,
    response: Response,
) -> StaffCreateResponse:
    """新建账号；同 ``(tenant_id, phone)`` 已有 ``inactive`` 账号时复活该行
    （200 ``revived=true``）。

    * ``active`` 同手机号 → 409 ``PHONE_IN_USE``；
    * ``teacher`` 必须带 ``teacher_id``、``registrar``/``admin`` 必须为空，
      且档案须本租户存在。
    """

    result = await create_staff_account(
        db,
        tenant_id=principal.tenant_id,
        name=payload.name,
        phone=payload.phone,
        role=payload.role,
        password=payload.password,
        teacher_id=payload.teacher_id,
    )
    response.status_code = 200 if result.revived else 201
    account = result.account
    return StaffCreateResponse(
        id=account.id,
        name=account.name,
        phone=account.phone,
        role=account.role,
        status=account.status,
        teacher_id=account.teacher_id,
        created_at=account.created_at,
        revived=result.revived,
    )


@router.get("", response_model=StaffList, summary="员工账号列表")
async def list_staff(
    db: TenantDbSession,
    principal: AdminOnly,
    page: Page = 1,
    page_size: PageSize = 20,
) -> StaffList:
    """分页列出本机构全部员工账号（不做模糊搜索，10 人机构够用）。"""

    rows, total = await list_staff_accounts(
        db, tenant_id=principal.tenant_id, page=page, page_size=page_size
    )
    return StaffList(items=[StaffOut.model_validate(row) for row in rows], total=total)


@router.patch(
    "/{staff_id}",
    response_model=StaffOut,
    summary="管理员工账号（停用/启用/重置密码/改角色）",
)
async def update_staff(
    staff_id: UUID,
    payload: StaffUpdate,
    db: TenantDbSession,
    principal: AdminOnly,
) -> StaffOut:
    """部分更新。``password`` 字段即「重置密码」（8~72 字符）。

    防呆：不能停用自己（``CANNOT_DISABLE_SELF``）；不能停用/降级本机构最后一个 active
    admin（``LAST_ADMIN``）；跨租户 → 404（RLS + 显式判断双保险）。
    """

    changes = _changes(payload)
    account = await update_staff_account(
        db,
        tenant_id=principal.tenant_id,
        actor_id=principal.subject_id,  # type: ignore[arg-type]
        staff_id=staff_id,
        changes=changes,
    )
    return StaffOut.model_validate(account)


@account_router.delete(
    "/account",
    status_code=204,
    summary="员工自助注销（需验证当前密码）",
)
async def delete_my_account(
    payload: SelfDeactivateRequest,
    db: DbSession,
    principal: CurrentPrincipal,
) -> Response:
    """本人置 ``status='inactive'``，历史记录保留；同手机号之后可被管理员复活。

    * 密码错 → 401 ``AUTH_FAILED``；最后一个 active admin → 403 ``LAST_ADMIN``；
    * ``platform_admin`` / 家长 → 403 ``OUT_OF_SCOPE``。
    """

    await self_deactivate(db, principal, payload.password)
    return Response(status_code=204)
