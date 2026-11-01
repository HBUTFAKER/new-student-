"""Platform administration endpoints (provisioning foundation)."""

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import DbSession
from app.api.permissions import require_role
from app.schemas.platform import (
    RegisterRequest,
    RegisterResponse,
    TenantCreateRequest,
    TenantCreateResponse,
    TenantUsageResponse,
)
from app.schemas.subscription import RenewRequest, RenewResponse
from app.services.subscription import (
    count_active_students,
    current_period,
    get_month_message_usage,
    get_plan_limits,
    get_subscription_status,
    owner_session,
    renew_subscription,
)
from app.services.tenant_provisioning import TRIAL_DAYS, provision_tenant

router = APIRouter(prefix="/platform", tags=["platform"])


@router.post(
    "/tenants",
    response_model=TenantCreateResponse,
    status_code=201,
    summary="开通租户并初始化管理员",
    dependencies=[Depends(require_role("platform_admin"))],
)
async def create_tenant(payload: TenantCreateRequest, db: DbSession) -> TenantCreateResponse:
    provisioned = await provision_tenant(
        db,
        name=payload.name,
        admin_name=payload.admin_name,
        admin_phone=payload.admin_phone,
        admin_password=payload.admin_password,
    )
    return TenantCreateResponse(
        tenant_id=provisioned.tenant_id,
        name=provisioned.name,
        admin_staff_id=provisioned.admin_staff_id,
        admin_phone=provisioned.admin_phone,
        plan_id=provisioned.plan_id,
    )


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=201,
    summary="公开自助注册开通（试用 14 天）",
)
async def register(payload: RegisterRequest, db: DbSession) -> RegisterResponse:
    """Self-serve onboarding. No auth, no token: the admin logs in via /auth/login.

    Reuses ``provision_tenant`` with the trial plan and a 14-day window; the
    response deliberately leaks no tenant data beyond the fields listed here.
    """

    provisioned = await provision_tenant(
        db,
        name=payload.org_name,
        admin_name=payload.admin_name,
        admin_phone=payload.admin_phone,
        admin_password=payload.admin_password,
        trial=True,
        subscription_days=TRIAL_DAYS,
    )
    return RegisterResponse(
        tenant_id=provisioned.tenant_id,
        name=provisioned.name,
        admin_phone=provisioned.admin_phone,
        plan_id=provisioned.plan_id,
        trial_end_date=provisioned.subscription_end_date,
    )


@router.post(
    "/tenants/{tenant_id}/renew",
    response_model=RenewResponse,
    summary="手动续费（平台管理员）",
    dependencies=[Depends(require_role("platform_admin"))],
)
async def renew_tenant(tenant_id: UUID, payload: RenewRequest) -> RenewResponse:
    """7.2：从 ``max(today, end_date)`` 起顺延 ``days`` 天；带 ``plan_id`` 则同时换套餐。

    续费即时生效（无缓存）。本步只做接口，web UI 归 7.4 平台后台。
    """

    async with owner_session() as db:
        async with db.begin():
            result = await renew_subscription(
                db,
                tenant_id=tenant_id,
                days=payload.days,
                plan_id=payload.plan_id,
            )
    return RenewResponse(
        end_date=result.end_date,
        plan_id=result.plan_id,
        plan_name=result.plan_name,
    )


@router.get(
    "/tenants/{tenant_id}/usage",
    response_model=TenantUsageResponse,
    summary="租户用量（平台管理员，7.3）",
    dependencies=[Depends(require_role("platform_admin"))],
)
async def tenant_usage(tenant_id: UUID) -> TenantUsageResponse:
    """7.4 平台后台租户详情页数据源：套餐 + 学生/消息用量 + 到期日。

    本步只做接口不做 UI。读的都是平台层表（plan/tenant_subscription/usage_meter），
    统一走 owner 连接绕 RLS；跨租户由 ``tenant_id`` 显式定位，无 RLS 兜底，靠
    ``require_role("platform_admin")`` 收口。
    """

    async with owner_session() as db:
        status = await get_subscription_status(db, tenant_id)
        limits = await get_plan_limits(db, tenant_id)
        students_used = await count_active_students(db, tenant_id)
        messages_used = await get_month_message_usage(db, tenant_id)
    return TenantUsageResponse(
        plan_name=status.plan_name,
        max_students=limits.max_students if limits else 0,
        students_used=students_used,
        max_messages=limits.max_messages if limits else 0,
        messages_used=messages_used,
        period=current_period(),
        subscription_end_date=status.end_date,
    )
