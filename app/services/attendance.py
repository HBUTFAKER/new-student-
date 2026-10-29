"""Makeup scheduling (3.4, US-212): seat an absent student on a future lesson.

This is the *scheduling* half of make-up, split from the *funding* half (2.5's
``create_makeup``, which writes a ``grant`` ledger row). Scheduling only writes
an ``attendance`` row with status ``makeup`` on the target lesson; it must never
touch ``lesson_ledger``. The funding half already lives in ``app.services.trade``
and is out of scope here.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.archive import Student
from app.models.attendance import Attendance
from app.models.schedule import Lesson
from app.services.ledger import BUSINESS_TZ

# 缺勤/请假才算"缺"，present 与 makeup 都不构成补课前提。
ABSENT_STATUSES = frozenset({"absent", "leave"})


async def schedule_makeup(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    student_id: UUID,
    absent_lesson_id: UUID,
    target_lesson_id: UUID,
    operator_id: UUID,
) -> Attendance:
    """Schedule one absence onto a future lesson by adding a ``makeup`` seat.

    The seat is the only thing written. The ``(tenant_id, lesson_id, student_id)``
    unique key on ``attendance`` is the idempotency guard: a student can hold at
    most one seat per target lesson, so a repeated request (or a concurrent one)
    cannot double-book the same slot.
    """

    if await db.get(Student, student_id) is None:
        raise AppError(404, "STUDENT_NOT_FOUND", "学生不存在")

    absent = await db.get(Lesson, absent_lesson_id)
    if absent is None:
        raise AppError(404, "LESSON_NOT_FOUND", "缺勤课次不存在")
    target = await db.get(Lesson, target_lesson_id)
    if target is None:
        raise AppError(404, "LESSON_NOT_FOUND", "补课课次不存在")
    if target.id == absent.id:
        raise AppError(422, "MAKEUP_TARGET_INVALID", "补课课次不能是缺勤课次本身")
    if target.status != "normal" or target.start_at <= datetime.now(BUSINESS_TZ):
        raise AppError(422, "MAKEUP_TARGET_INVALID", "补课课次必须是未来的正常课次")

    absence = await db.scalar(
        select(Attendance).where(
            Attendance.lesson_id == absent.id,
            Attendance.student_id == student_id,
        )
    )
    if absence is None or absence.status not in ABSENT_STATUSES:
        raise AppError(422, "ABSENCE_NOT_FOUND", "该学员在缺勤课次没有缺勤/请假记录")

    existing = await db.scalar(
        select(Attendance.id).where(
            Attendance.lesson_id == target.id,
            Attendance.student_id == student_id,
        )
    )
    if existing is not None:
        raise AppError(409, "MAKEUP_ALREADY_SCHEDULED", "同一次缺勤只能排一次补课")

    seat = Attendance(
        tenant_id=tenant_id,
        lesson_id=target.id,
        student_id=student_id,
        status="makeup",
        deduct_lessons=0,
        operator_id=operator_id,
    )
    db.add(seat)
    try:
        await db.flush()
    except IntegrityError as exc:
        # 并发下唯一键兜底：同目标课次同一学员已有座位。
        raise AppError(409, "MAKEUP_ALREADY_SCHEDULED", "同一次缺勤只能排一次补课") from exc
    return seat
