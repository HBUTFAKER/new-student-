"""Organisation config endpoints (3.5, US-217): the absence deduction rule.

读：教务/管理员 ✅，老师可读（点名界面要提示会不会扣课）；写：仅 admin/registrar
（0.2 矩阵「请假/缺勤扣课规则配置」老师 ❌）。家长两个方向都 403。
"""

from fastapi import APIRouter

from app.api.deps import TenantDbSession
from app.api.permissions import OfficeUser, TeacherOrOffice
from app.schemas.attendance import AbsenceRuleOut, AbsenceRuleUpdate
from app.services.org_config import (
    DEFAULT_ABSENCE_RULE,
    get_absence_config,
    update_absence_rule,
)

router = APIRouter(tags=["org-config"])


@router.get(
    "/org-config/absence-deduct-rule",
    response_model=AbsenceRuleOut,
    summary="请假/缺勤是否扣课",
)
async def read_absence_rule(db: TenantDbSession, principal: TeacherOrOffice) -> AbsenceRuleOut:
    config = await get_absence_config(db)
    if config is None:
        return AbsenceRuleOut(absence_deduct_rule=DEFAULT_ABSENCE_RULE)
    return AbsenceRuleOut(
        absence_deduct_rule=config.absence_deduct_rule,
        updated_by=config.updated_by,
        updated_at=config.updated_at,
    )


@router.patch(
    "/org-config/absence-deduct-rule",
    response_model=AbsenceRuleOut,
    summary="修改请假/缺勤扣课规则（留痕）",
)
async def patch_absence_rule(
    payload: AbsenceRuleUpdate, db: TenantDbSession, principal: OfficeUser
) -> AbsenceRuleOut:
    config = await update_absence_rule(
        db,
        tenant_id=principal.tenant_id,
        rule=payload.absence_deduct_rule,
        operator_id=principal.subject_id,
    )
    return AbsenceRuleOut(
        absence_deduct_rule=config.absence_deduct_rule,
        updated_by=config.updated_by,
        updated_at=config.updated_at,
    )
