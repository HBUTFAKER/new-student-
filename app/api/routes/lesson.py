"""Lesson endpoints (3.2 / 3.4): generation, timetable query, single add, move/cancel, makeup.

写操作（生成/加课/调课/取消/补课排期）收口 OfficeUser；查询本步同 3.1 口径
（老师/家长按 0.2 矩阵的「课表查询」ⓘ 收窄属于后续课表接口，这里不做半成品）。
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantDbSession
from app.api.permissions import OfficeUser
from app.core.errors import AppError
from app.models.schedule import Lesson
from app.schemas.lesson import (
    LessonCreate,
    LessonGenerate,
    LessonGenerationOut,
    LessonList,
    LessonOut,
    LessonUpdate,
    MakeupLessonCreate,
    MakeupLessonOut,
)
from app.services.attendance import schedule_makeup
from app.services.ledger import BUSINESS_TZ
from app.services.lesson import (
    create_lesson,
    generate_lessons,
    lesson_select,
    list_lessons,
    update_lesson,
)

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


async def _lesson_out(db: AsyncSession, lesson_id: UUID) -> LessonOut:
    row = (await db.execute(lesson_select().where(Lesson.id == lesson_id))).first()
    if row is None:
        raise AppError(404, "LESSON_NOT_FOUND", "课次不存在")
    return _lesson_view(row)


@router.post(
    "/lessons", status_code=201, response_model=LessonOut, summary="单次加课（教务/管理员）"
)
async def create_lesson_endpoint(
    payload: LessonCreate, db: TenantDbSession, principal: OfficeUser
) -> LessonOut:
    lesson = await create_lesson(
        db,
        tenant_id=principal.tenant_id,
        class_id=payload.class_id,
        classroom_id=payload.classroom_id,
        teacher_id=payload.teacher_id,
        start_at=payload.start_at,
        end_at=payload.end_at,
    )
    return await _lesson_out(db, lesson.id)


@router.patch("/lessons/{lesson_id}", response_model=LessonOut, summary="调课/取消（教务/管理员）")
async def patch_lesson(
    lesson_id: UUID,
    payload: LessonUpdate,
    db: TenantDbSession,
    principal: OfficeUser,
) -> LessonOut:
    await update_lesson(db, lesson_id=lesson_id, data=payload.model_dump(exclude_unset=True))
    return await _lesson_out(db, lesson_id)


@router.post(
    "/makeup-lessons",
    status_code=201,
    response_model=MakeupLessonOut,
    summary="补课排期（教务/管理员）",
)
async def schedule_makeup_endpoint(
    payload: MakeupLessonCreate, db: TenantDbSession, principal: OfficeUser
) -> MakeupLessonOut:
    seat = await schedule_makeup(
        db,
        tenant_id=principal.tenant_id,
        student_id=payload.student_id,
        absent_lesson_id=payload.absent_lesson_id,
        target_lesson_id=payload.target_lesson_id,
        operator_id=principal.subject_id,
    )
    return MakeupLessonOut(
        id=seat.id,
        student_id=seat.student_id,
        target_lesson_id=seat.lesson_id,
        status=seat.status,
    )
