"""Lesson endpoints (3.2): batch generation and timetable query.

写操作（生成）收口 OfficeUser；查询本步同 3.1 口径（老师/家长按 0.2 矩阵的
「课表查询」ⓘ 收窄属于后续课表接口，这里不做半成品）。
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import Row

from app.api.deps import TenantDbSession
from app.api.permissions import OfficeUser
from app.schemas.lesson import LessonGenerate, LessonGenerationOut, LessonList, LessonOut
from app.services.ledger import BUSINESS_TZ
from app.services.lesson import generate_lessons, list_lessons

router = APIRouter(tags=["lesson"])

Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


def _lesson_view(row: Row) -> LessonOut:
    lesson, class_name, teacher_name, classroom_name = row
    return LessonOut(
        id=lesson.id,
        class_id=lesson.class_id,
        class_name=class_name,
        classroom_id=lesson.classroom_id,
        classroom_name=classroom_name,
        teacher_id=lesson.teacher_id,
        teacher_name=teacher_name,
        # 统一用业务时区回给前端，别让客户端猜服务器时区。
        start_at=lesson.start_at.astimezone(BUSINESS_TZ),
        end_at=lesson.end_at.astimezone(BUSINESS_TZ),
        status=lesson.status,
    )


@router.post("/lessons/generate", response_model=LessonGenerationOut, summary="按规律批量生成课次")
async def generate_lessons_endpoint(
    payload: LessonGenerate, db: TenantDbSession, principal: OfficeUser
) -> LessonGenerationOut:
    result = await generate_lessons(
        db, tenant_id=principal.tenant_id, class_id=payload.class_id, weeks=payload.weeks
    )
    return LessonGenerationOut(
        class_id=result.class_id,
        from_date=result.from_date,
        to_date=result.to_date,
        generated=result.generated,
        skipped_holidays=result.skipped_holidays,
        skipped_existing=result.skipped_existing,
        skipped_conflicts=result.skipped_conflicts,
        conflicts=[clash.to_dict() for clash in result.conflicts],
    )


@router.get("/lessons", response_model=LessonList, summary="课次列表（按班/日期区间分页）")
async def list_lessons_endpoint(
    db: TenantDbSession,
    principal: OfficeUser,
    class_id: UUID | None = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    page: Page = 1,
    page_size: PageSize = 20,
) -> LessonList:
    rows, total = await list_lessons(
        db,
        class_id=class_id,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
    return LessonList(items=[_lesson_view(row) for row in rows], total=total)
