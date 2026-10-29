"""Platform administration endpoints (provisioning foundation)."""

from fastapi import APIRouter, Depends

from app.api.deps import DbSession
from app.api.permissions import require_role
from app.schemas.platform import TenantCreateRequest, TenantCreateResponse
from app.services.tenant_provisioning import provision_tenant

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
