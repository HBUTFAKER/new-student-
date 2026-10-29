"""Schedule rule endpoints (3.1, US-207 recurrence part).

0.2 矩阵：规律排课只对教务/机构管理员开放（家长、老师均 ❌），写操作同样收口
到 ``OfficeUser``；老师的课表查询走后续的 ``GET /lessons``（3.2 之后）。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response
from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantDbSession
from app.api.permissions import OfficeUser
from app.core.errors import AppError
from app.models.schedule import ScheduleRule
from app.schemas.schedule import (
    ScheduleRuleCreate,
    ScheduleRuleList,
    ScheduleRuleOut,
    ScheduleRuleUpdate,
)
from app.services.schedule import (
    create_rules,
    delete_rule,
    list_rules,
    rule_select,
    update_rule,
)

router = APIRouter(tags=["schedule"])

Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


def _rule_view(row: Row) -> ScheduleRuleOut:
    rule, class_name, teacher_name, classroom_name = row
    return ScheduleRuleOut(
        id=rule.id,
        class_id=rule.class_id,
        class_name=class_name,
        classroom_id=rule.classroom_id,
        classroom_name=classroom_name,
        teacher_id=rule.teacher_id,
        teacher_name=teacher_name,
        weekday=rule.weekday,
        start_time=rule.start_time,
        end_time=rule.end_time,
        start_date=rule.start_date,
        end_date=rule.end_date,
        created_at=rule.created_at,
    )


async def _rule_out(db: AsyncSession, rule_id: UUID) -> ScheduleRuleOut:
    row = (await db.execute(rule_select().where(ScheduleRule.id == rule_id))).first()
    if row is None:
        raise AppError(404, "SCHEDULE_RULE_NOT_FOUND", "排课规律不存在")
    return _rule_view(row)


@router.post(
    "/schedule-rules",
    response_model=ScheduleRuleList,
    status_code=201,
    summary="新增排课规律（一次课可多天，如每周二四）",
)
async def create_schedule_rules(
    payload: ScheduleRuleCreate, db: TenantDbSession, principal: OfficeUser
) -> ScheduleRuleList:
    rules = await create_rules(
        db,
        tenant_id=principal.tenant_id,
        class_id=payload.class_id,
        classroom_id=payload.classroom_id,
        teacher_id=payload.teacher_id,
        weekdays=payload.resolved_weekdays(),
        start_time=payload.start_time,
        end_time=payload.end_time,
        start_date=payload.start_date,
        end_date=payload.resolved_end_date(),
    )
    rows = await db.execute(rule_select().where(ScheduleRule.id.in_([rule.id for rule in rules])))
    views = {row[0].id: _rule_view(row) for row in rows}
    return ScheduleRuleList(items=[views[rule.id] for rule in rules], total=len(rules))


@router.get("/schedule-rules", response_model=ScheduleRuleList, summary="排课规律列表")
async def list_schedule_rules(
    db: TenantDbSession,
    principal: OfficeUser,
    class_id: UUID | None = None,
    teacher_id: UUID | None = None,
    classroom_id: UUID | None = None,
    weekday: Annotated[int | None, Query(ge=0, le=6)] = None,
    page: Page = 1,
    page_size: PageSize = 20,
) -> ScheduleRuleList:
    rows, total = await list_rules(
        db,
        class_id=class_id,
        teacher_id=teacher_id,
        classroom_id=classroom_id,
        weekday=weekday,
        page=page,
        page_size=page_size,
    )
    return ScheduleRuleList(items=[_rule_view(row) for row in rows], total=total)


@router.get("/schedule-rules/{rule_id}", response_model=ScheduleRuleOut, summary="排课规律详情")
async def get_schedule_rule(
    rule_id: UUID, db: TenantDbSession, principal: OfficeUser
) -> ScheduleRuleOut:
    return await _rule_out(db, rule_id)


@router.patch("/schedule-rules/{rule_id}", response_model=ScheduleRuleOut, summary="更新排课规律")
async def update_schedule_rule(
    rule_id: UUID,
    payload: ScheduleRuleUpdate,
    db: TenantDbSession,
    principal: OfficeUser,
) -> ScheduleRuleOut:
    await update_rule(db, rule_id, payload.model_dump(exclude_unset=True))
    return await _rule_out(db, rule_id)


@router.delete("/schedule-rules/{rule_id}", status_code=204, summary="删除排课规律")
async def delete_schedule_rule(
    rule_id: UUID, db: TenantDbSession, principal: OfficeUser
) -> Response:
    await delete_rule(db, rule_id)
    return Response(status_code=204)
