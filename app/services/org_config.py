"""Per-tenant configuration (3.5): the absence deduction rule (US-217).

One row per tenant, created by tenant provisioning. ``none`` means a leave or an
absence costs the student nothing; ``deduct`` means it costs one lesson, exactly
like a present. The rule is read on every check-in, so a change applies to the
next request without any cache to invalidate.

Change trail: ``updated_by`` / ``updated_at`` are the minimal audit required by
US-217. A full audit-log module (US-304) is a later phase.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.org import OrgConfig
from app.services.ledger import BUSINESS_TZ

DEFAULT_ABSENCE_RULE = "none"
ABSENCE_RULES = frozenset({"none", "deduct"})


async def get_absence_config(db: AsyncSession) -> OrgConfig | None:
    """The tenant's config row (RLS scopes it to the current tenant)."""

    return await db.scalar(select(OrgConfig))


async def get_absence_rule(db: AsyncSession) -> str:
    config = await get_absence_config(db)
    return config.absence_deduct_rule if config is not None else DEFAULT_ABSENCE_RULE


async def update_absence_rule(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    rule: str,
    operator_id: UUID,
) -> OrgConfig:
    """Persist the rule and stamp who changed it.

    The caller is restricted to admin/registrar by the route, so a teacher can
    never flip the rule that decides whether their students lose a lesson.
    """

    if rule not in ABSENCE_RULES:
        raise AppError(422, "RULE_VALUE_INVALID", "absence_deduct_rule 只能是 none 或 deduct")

    config = await get_absence_config(db)
    if config is None:
        # Provisioning always creates the row; this keeps a hand-made tenant usable.
        config = OrgConfig(tenant_id=tenant_id, absence_deduct_rule=rule)
        db.add(config)
    else:
        config.absence_deduct_rule = rule
    config.updated_by = operator_id
    config.updated_at = datetime.now(BUSINESS_TZ)
    await db.flush()
    return config
