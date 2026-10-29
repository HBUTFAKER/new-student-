"""Tenant provisioning: tenant + subscription + defaults + first admin atomically."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import hash_password
from app.models.org import OrgConfig, StaffAccount
from app.models.platform import Plan, Tenant, TenantSubscription


@dataclass(frozen=True, slots=True)
class ProvisionedTenant:
    tenant_id: UUID
    name: str
    admin_staff_id: UUID
    admin_phone: str
    plan_id: UUID


async def _ensure_default_plan(db: AsyncSession) -> UUID:
    """Return the first plan, creating a free base plan when none exists."""

    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('platform_default_plan'))"))
    plan_id = await db.scalar(text("SELECT id FROM plan ORDER BY created_at, id LIMIT 1"))
    if plan_id is not None:
        return plan_id

    plan = Plan(
        name="基础版",
        max_students=100,
        max_messages=1000,
        price=Decimal("0.00"),
    )
    db.add(plan)
    await db.flush()
    return plan.id


async def _create_admin_account(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    admin_name: str,
    admin_phone: str,
    password_hash: str,
) -> UUID:
    staff = StaffAccount(
        tenant_id=tenant_id,
        name=admin_name,
        phone=admin_phone,
        password_hash=password_hash,
        role="admin",
        status="active",
    )
    db.add(staff)
    await db.flush()
    return staff.id


async def provision_tenant(
    db: AsyncSession,
    *,
    name: str,
    admin_name: str,
    admin_phone: str,
    admin_password: str,
) -> ProvisionedTenant:
    """Create everything in one transaction; a failure leaves no half tenant.

    The advisory lock serialises concurrent requests for the same display name,
    providing idempotency without making tenant names a global unique key (0.4
    allows different organisations to share a display name in general).
    """

    password_hash = hash_password(admin_password)

    async with db.begin():
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:name))"), {"name": name})
        exists = await db.scalar(
            text("SELECT 1 FROM tenant WHERE name = :name LIMIT 1"), {"name": name}
        )
        if exists:
            raise AppError(409, "TENANT_NAME_EXISTS", "同名租户已存在")

        plan_id = await _ensure_default_plan(db)
        tenant = Tenant(name=name)
        db.add(tenant)
        await db.flush()

        today = date.today()
        db.add(
            TenantSubscription(
                tenant_id=tenant.id,
                plan_id=plan_id,
                start_date=today,
                end_date=today + timedelta(days=365),
                status="active",
            )
        )

        await db.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant.id)},
        )
        db.add(OrgConfig(tenant_id=tenant.id, absence_deduct_rule="none"))
        admin_staff_id = await _create_admin_account(
            db,
            tenant_id=tenant.id,
            admin_name=admin_name,
            admin_phone=admin_phone,
            password_hash=password_hash,
        )

        return ProvisionedTenant(
            tenant_id=tenant.id,
            name=tenant.name,
            admin_staff_id=admin_staff_id,
            admin_phone=admin_phone,
            plan_id=plan_id,
        )
