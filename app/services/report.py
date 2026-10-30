"""Read-only report aggregation (4.4, US-213/215/302 一期扩容).

Three queries, all ``GROUP BY`` in PostgreSQL — detail rows are never pulled into
Python to be summed. Nothing here writes: reports are a pure projection of
``lesson`` / ``attendance`` / ``lesson_ledger``.

Time windows are half-open ``[from, to)`` and are normalised to ``BUSINESS_TZ``
(UTC+8) before they hit the database, so "11 月的课耗" cannot drift into October
because of the server's timezone.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.archive import Student, Teacher
from app.models.attendance import Attendance
from app.models.course import Class
from app.models.ledger import LessonLedger
from app.models.schedule import Lesson
from app.services.ledger import BUSINESS_TZ

# 单次统计窗口上限：一年（含闰日余量），超出直接拒绝，避免全表扫。
MAX_SPAN = timedelta(days=366)

# 计入报表的课次状态：取消的课次不占时段、不出现在任何数字里。
COUNTED_LESSON_STATUSES = ("normal", "moved")

# 出勤：present 与 makeup 都算"到课"（makeup 是 3.4 的预登记态，正常会被
# 3.5 原地转正；万一残留，按到课计并在响应 notes 里注明）。
PRESENT_STATUSES = ("present", "makeup")


@dataclass(frozen=True, slots=True)
class ClassAttendance:
    class_id: UUID
    class_name: str
    lesson_count: int
    present: int
    leave: int
    absent: int
    attendance_rate: int | None


@dataclass(frozen=True, slots=True)
class StudentAttendance:
    student_id: UUID
    student_name: str
    present: int
    leave: int
    absent: int
    attendance_rate: int | None


@dataclass(frozen=True, slots=True)
class AttendanceSummary:
    items: list[ClassAttendance]
    student_rows: list[StudentAttendance]
    makeup_rows: int


@dataclass(frozen=True, slots=True)
class StudentConsumption:
    student_id: UUID
    student_name: str
    total_consumed: int
    lesson_count: int


@dataclass(frozen=True, slots=True)
class ClassConsumption:
    student_id: UUID
    class_id: UUID | None
    class_name: str | None
    consumed: int
    lesson_count: int


@dataclass(frozen=True, slots=True)
class Consumption:
    items: list[StudentConsumption]
    by_class: list[ClassConsumption]


@dataclass(frozen=True, slots=True)
class TeacherHours:
    teacher_id: UUID
    teacher_name: str
    lesson_count: int
    completed_count: int
    total_minutes: int


def resolve_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """Validate ``[from, to)`` and pin it to the business timezone."""

    if start.tzinfo is None or end.tzinfo is None:
        raise AppError(422, "TIMEZONE_REQUIRED", "时间参数必须带时区，如 2026-11-01T00:00:00+08:00")

    start = start.astimezone(BUSINESS_TZ)
    end = end.astimezone(BUSINESS_TZ)

    if start >= end:
        raise AppError(422, "RANGE_INVALID", "开始时间必须早于结束时间")
    if end - start > MAX_SPAN:
        raise AppError(422, "RANGE_TOO_LARGE", "统计跨度不能超过 366 天")
    return start, end


def _rate(present: int, leave: int, absent: int) -> int | None:
    """``present / (present + leave + absent)`` as a half-up integer percentage.

    Returns ``None`` when nobody was recorded — "no data" and "0%" are different
    facts and the UI must be able to tell them apart.
    """

    denominator = present + leave + absent
    if denominator == 0:
        return None
    quotient, remainder = divmod(present * 100, denominator)
    if remainder * 2 >= denominator:
        quotient += 1
    return quotient


def _counted_lesson_filters(start: datetime, end: datetime, class_id: UUID | None) -> list:
    filters = [
        Lesson.status.in_(COUNTED_LESSON_STATUSES),
        Lesson.start_at >= start,
        Lesson.start_at < end,
    ]
    if class_id is not None:
        filters.append(Lesson.class_id == class_id)
    return filters


async def attendance_summary(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    class_id: UUID | None = None,
    detail: bool = False,
) -> AttendanceSummary:
    """Per-class attendance over lessons that had already started in the window."""

    filters = _counted_lesson_filters(start, end, class_id)
    # 只统计"已开始"的课次：给未来的课打考勤是预演，不该进报表。
    filters.append(Lesson.start_at <= func.now())

    rows = await db.execute(
        select(
            Lesson.class_id,
            Class.name,
            func.count(distinct(Lesson.id)),
            func.count(Attendance.id).filter(Attendance.status.in_(PRESENT_STATUSES)),
            func.count(Attendance.id).filter(Attendance.status == "leave"),
            func.count(Attendance.id).filter(Attendance.status == "absent"),
            func.count(Attendance.id).filter(Attendance.status == "makeup"),
        )
        .select_from(Lesson)
        .join(Class, Class.id == Lesson.class_id)
        .outerjoin(Attendance, Attendance.lesson_id == Lesson.id)
        .where(*filters)
        .group_by(Lesson.class_id, Class.name)
        .order_by(Class.name, Lesson.class_id)
    )

    items: list[ClassAttendance] = []
    makeup_rows = 0
    for row in rows:
        class_id_, class_name, lesson_count, present, leave, absent, makeup = row
        makeup_rows += makeup
        items.append(
            ClassAttendance(
                class_id=class_id_,
                class_name=class_name,
                lesson_count=lesson_count,
                present=present,
                leave=leave,
                absent=absent,
                attendance_rate=_rate(present, leave, absent),
            )
        )

    student_rows: list[StudentAttendance] = []
    if detail:
        student_rows = await _attendance_by_student(db, start=start, end=end, class_id=class_id)

    return AttendanceSummary(items=items, student_rows=student_rows, makeup_rows=makeup_rows)


async def _attendance_by_student(
    db: AsyncSession, *, start: datetime, end: datetime, class_id: UUID | None
) -> list[StudentAttendance]:
    filters = _counted_lesson_filters(start, end, class_id)
    filters.append(Lesson.start_at <= func.now())

    rows = await db.execute(
        select(
            Student.id,
            Student.name,
            func.count(Attendance.id).filter(Attendance.status.in_(PRESENT_STATUSES)),
            func.count(Attendance.id).filter(Attendance.status == "leave"),
            func.count(Attendance.id).filter(Attendance.status == "absent"),
        )
        .select_from(Attendance)
        .join(Lesson, Lesson.id == Attendance.lesson_id)
        .join(Student, Student.id == Attendance.student_id)
        .where(*filters)
        .group_by(Student.id, Student.name)
        .order_by(Student.name, Student.id)
    )

    return [
        StudentAttendance(
            student_id=student_id,
            student_name=name,
            present=present,
            leave=leave,
            absent=absent,
            attendance_rate=_rate(present, leave, absent),
        )
        for student_id, name, present, leave, absent in rows
    ]


async def consumption(db: AsyncSession, *, start: datetime, end: datetime) -> Consumption:
    """Lesson consumption from the append-only ledger (``type='consume'``)."""

    window = [LessonLedger.created_at >= start, LessonLedger.created_at < end]
    consumed = (-LessonLedger.delta).label("consumed")

    rows = await db.execute(
        select(
            Student.id,
            Student.name,
            func.sum(consumed),
            func.count(LessonLedger.id),
        )
        .select_from(LessonLedger)
        .join(Student, Student.id == LessonLedger.student_id)
        .where(LessonLedger.type == "consume", *window)
        .group_by(Student.id, Student.name)
        .order_by(Student.name, Student.id)
    )
    items = [
        StudentConsumption(
            student_id=student_id,
            student_name=name,
            total_consumed=int(total),
            lesson_count=lesson_count,
        )
        for student_id, name, total, lesson_count in rows
    ]

    # 按班级分布：consume 流水自带 lesson_id，join lessons 取班级。
    # 异常行（lesson_id 为空）仍保留，班级列为 null，保证 by_class 之和 == items。
    by_class_rows = await db.execute(
        select(
            LessonLedger.student_id,
            Lesson.class_id,
            Class.name,
            func.sum(consumed),
            func.count(LessonLedger.id),
        )
        .select_from(LessonLedger)
        .outerjoin(Lesson, Lesson.id == LessonLedger.lesson_id)
        .outerjoin(Class, Class.id == Lesson.class_id)
        .where(LessonLedger.type == "consume", *window)
        .group_by(LessonLedger.student_id, Lesson.class_id, Class.name)
        .order_by(Class.name, LessonLedger.student_id)
    )
    by_class = [
        ClassConsumption(
            student_id=student_id,
            class_id=class_id_,
            class_name=class_name,
            consumed=int(total),
            lesson_count=lesson_count,
        )
        for student_id, class_id_, class_name, total, lesson_count in by_class_rows
    ]

    return Consumption(items=items, by_class=by_class)


async def teacher_hours(db: AsyncSession, *, start: datetime, end: datetime) -> list[TeacherHours]:
    """Teaching load per teacher, in minutes actually scheduled (not the course config)."""

    rows = await db.execute(
        select(
            Lesson.teacher_id,
            Teacher.name,
            func.count(Lesson.id),
            func.count(Lesson.id).filter(Lesson.start_at <= func.now()),
            func.sum(func.extract("epoch", Lesson.end_at - Lesson.start_at)),
        )
        .select_from(Lesson)
        .join(Teacher, Teacher.id == Lesson.teacher_id)
        .where(
            Lesson.status.in_(COUNTED_LESSON_STATUSES),
            Lesson.start_at >= start,
            Lesson.start_at < end,
        )
        .group_by(Lesson.teacher_id, Teacher.name)
        .order_by(Teacher.name, Lesson.teacher_id)
    )

    return [
        TeacherHours(
            teacher_id=teacher_id,
            teacher_name=name,
            lesson_count=lesson_count,
            completed_count=completed_count,
            total_minutes=int(round(seconds / 60)),
        )
        for teacher_id, name, lesson_count, completed_count, seconds in rows
    ]
