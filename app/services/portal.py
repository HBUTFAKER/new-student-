"""家长端只读三件套的服务层（05 §9.3–9.5，step 5.3）。

红线：本模块**只读**。不写 ``lesson_ledger``、不动签到、不动排课，也不重算余额口径——
余额与课时包直接复用 ``app/services/ledger.py`` 的 ``total_balance`` / ``student_packages``，
家长看到的数字必须与后台 ``/students/{id}/packages`` 完全一致。

课表取数的两条来源合并：
1. 孩子所在班级的课次（``class → class_enrollment`` 链路）；
2. 补课席位（``attendance.status = 'makeup'``）——补课可以排进孩子并未报名的班级，
   只查 enrollment 会漏掉这类课次。注意 ``lesson.status`` 本身没有 ``makeup``，
   补课是签到行的状态，所以出参里由 ``attendance`` 反推 ``status='makeup'``。
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.archive import Teacher
from app.models.attendance import Attendance
from app.models.course import Class, ClassEnrollment, Course
from app.models.ledger import LessonLedger
from app.models.schedule import Lesson
from app.services.ledger import BUSINESS_TZ, ledger_query, student_packages, total_balance

# 家长翻周看课表，窗口上限收在 31 天（比报表的 366 天紧），防全表拉取。
MAX_SCHEDULE_SPAN = timedelta(days=31)


@dataclass(frozen=True, slots=True)
class ScheduleEntry:
    lesson_id: UUID
    class_name: str
    subject: str | None
    teacher_name: str | None
    start_at: datetime
    end_at: datetime
    status: str


@dataclass(frozen=True, slots=True)
class BalancePackage:
    purchase_id: UUID
    remaining: int
    expires_at: date | None


@dataclass(frozen=True, slots=True)
class LedgerRow:
    type: str
    delta: int
    class_name: str | None
    created_at: datetime


def resolve_schedule_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """Validate ``[from, to)`` for the schedule, pinned to UTC+8.

    与报表 ``resolve_window`` 同口径（都按 ``BUSINESS_TZ`` 落时区、都要求带时区），
    差别只在跨度上限 31 天。
    """

    if start.tzinfo is None or end.tzinfo is None:
        raise AppError(422, "TIMEZONE_REQUIRED", "时间参数必须带时区，如 2026-11-03T00:00:00+08:00")

    start = start.astimezone(BUSINESS_TZ)
    end = end.astimezone(BUSINESS_TZ)

    if start >= end:
        raise AppError(422, "RANGE_INVALID", "开始时间必须早于结束时间")
    if end - start > MAX_SCHEDULE_SPAN:
        raise AppError(422, "RANGE_TOO_LARGE", "课表查询跨度不能超过 31 天")
    return start, end


async def child_schedule(
    db: AsyncSession, student_id: UUID, start: datetime, end: datetime
) -> list[ScheduleEntry]:
    """Window lessons for one child; ``cancelled`` never surfaces."""

    makeup_seats = select(Attendance.lesson_id).where(
        Attendance.student_id == student_id,
        Attendance.status == "makeup",
    )
    enrolled_classes = select(ClassEnrollment.class_id).where(
        ClassEnrollment.student_id == student_id
    )
    rows = await db.execute(
        select(
            Lesson.id,
            Lesson.start_at,
            Lesson.end_at,
            Lesson.status,
            Class.name,
            Course.subject,
            Course.name,
            Teacher.name,
            Lesson.id.in_(makeup_seats).label("is_makeup"),
        )
        .join(Class, Class.id == Lesson.class_id)
        .join(Course, Course.id == Class.course_id)
        .outerjoin(Teacher, Teacher.id == Class.teacher_id)
        .where(
            Lesson.status != "cancelled",
            Lesson.start_at >= start,
            Lesson.start_at < end,
            or_(Lesson.class_id.in_(enrolled_classes), Lesson.id.in_(makeup_seats)),
        )
        .order_by(Lesson.start_at.asc(), Lesson.id.asc())
    )
    entries: list[ScheduleEntry] = []
    for (
        lesson_id,
        start_at,
        end_at,
        status,
        class_name,
        subject,
        course_name,
        teacher,
        makeup,
    ) in rows:
        entries.append(
            ScheduleEntry(
                lesson_id=lesson_id,
                class_name=class_name,
                subject=subject or course_name,
                teacher_name=teacher,
                start_at=start_at.astimezone(BUSINESS_TZ),
                end_at=end_at.astimezone(BUSINESS_TZ),
                status="makeup" if makeup else status,
            )
        )
    return entries


async def child_balance(db: AsyncSession, student_id: UUID) -> tuple[int, list[BalancePackage]]:
    """``total`` 实时求和；``packages`` 只列仍有效的包（过期/耗尽的不进列表，但计入 total）。

    过期包没被列表带出来，可它的 grant 流水仍在 ``lesson_ledger`` 里，所以 ``total``
    天然含它——这与 ``deduct_lessons`` 的先过期先扣是同一套数据，不另算。
    """

    total = await total_balance(db, student_id)
    packages = await student_packages(db, student_id)
    active = [
        BalancePackage(
            purchase_id=item["purchase_id"],
            remaining=int(item["remaining"]),
            expires_at=item["expires_at"],
        )
        for item in packages
        if item["status"] == "active"
    ]
    return total, active


async def child_ledger(
    db: AsyncSession,
    student_id: UUID,
    *,
    ledger_type: str | None,
    page: int,
    page_size: int,
) -> tuple[list[LedgerRow], int]:
    """该孩子全部类型的流水，分页；口径与 ``/students/{id}/ledger`` 一致。"""

    stmt = ledger_query(student_id)
    if ledger_type is not None:
        stmt = stmt.where(LessonLedger.type == ledger_type)

    counted = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = int(await db.scalar(counted) or 0)
    rows = await db.execute(
        stmt.order_by(LessonLedger.created_at.desc(), LessonLedger.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        LedgerRow(
            type=entry.type,
            delta=entry.delta,
            class_name=class_name,
            created_at=entry.created_at.astimezone(BUSINESS_TZ),
        )
        for entry, class_name in rows
    ]
    return items, total
