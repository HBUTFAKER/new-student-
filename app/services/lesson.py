"""Lesson generation from schedule rules (3.2) and single insertion (3.3).

Timezone: ``lesson.start_at`` is ``TIMESTAMPTZ``. Every value built here is
aware and pinned to the same UTC+8 business zone the ledger uses for expiry
dates, so "周二 18:00" always means 18:00 in Wuhan no matter where the server
runs. Naive datetimes never reach the database.

Idempotency: the existing ``start_at`` set of the class is read once before the
loop, and every accepted candidate is added to that set immediately, so a
repeated call (or two rules landing on the same slot) inserts nothing twice.

Conflict detection (3.3): the generator runs ``find_conflicts`` on every
candidate and *skips* clashing slots (counting ``skipped_conflicts``) instead of
failing the whole batch. The EXCLUDE constraints stay as a database backstop for
the concurrency window where two transactions both pass the pre-check; a flush
that hits exclusion violation ``23P01`` is surfaced as 409 ``SCHEDULE_CONFLICT``.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import Row, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.archive import Classroom, Teacher
from app.models.course import Class
from app.models.schedule import Lesson, ScheduleRule
from app.services.conflict import Conflict, find_conflicts
from app.services.holiday import holiday_dates
from app.services.ledger import BUSINESS_TZ, business_today


@dataclass(frozen=True, slots=True)
class GenerationResult:
    class_id: UUID
    from_date: date
    to_date: date
    generated: int
    skipped_holidays: int
    skipped_existing: int
    skipped_conflicts: int = 0
    conflicts: list[Conflict] = field(default_factory=list)


def day_start(day: date) -> datetime:
    """Midnight of a business day as an aware datetime (UTC+8)."""

    return datetime.combine(day, time.min, tzinfo=BUSINESS_TZ)


async def generate_lessons(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    class_id: UUID,
    weeks: int,
    today: date | None = None,
) -> GenerationResult:
    """Expand every rule of the class into lessons for ``weeks`` weeks."""

    klass = await db.get(Class, class_id)
    if klass is None:
        raise AppError(404, "CLASS_NOT_FOUND", "班级不存在")
    if klass.status == "ended":
        raise AppError(409, "CLASS_ENDED", "班级已结课，不能再生成课次")

    rules = list(
        await db.scalars(
            select(ScheduleRule)
            .where(ScheduleRule.class_id == class_id)
            .order_by(ScheduleRule.weekday, ScheduleRule.start_time, ScheduleRule.id)
        )
    )
    if not rules:
        raise AppError(409, "NO_SCHEDULE_RULE", "该班还没有排课规律，请先配置规律")

    today = today or business_today()
    from_date = max(max(rule.start_date for rule in rules), today)
    to_date = from_date + timedelta(days=7 * weeks - 1)

    holidays = await holiday_dates(db, from_date, to_date)
    booked = set(
        await db.scalars(
            select(Lesson.start_at).where(
                Lesson.class_id == class_id,
                Lesson.start_at >= day_start(from_date),
                Lesson.start_at < day_start(to_date + timedelta(days=1)),
            )
        )
    )

    generated = skipped_holidays = skipped_existing = skipped_conflicts = 0
    conflicts: list[Conflict] = []
    lessons: list[Lesson] = []
    day = from_date
    while day <= to_date:
        for rule in rules:
            if rule.weekday != day.weekday():
                continue
            if day < rule.start_date:
                continue
            if rule.end_date is not None and day > rule.end_date:
                continue
            if day in holidays:
                skipped_holidays += 1
                continue
            start_at = datetime.combine(day, rule.start_time, tzinfo=BUSINESS_TZ)
            if start_at in booked:
                skipped_existing += 1
                continue
            end_at = datetime.combine(day, rule.end_time, tzinfo=BUSINESS_TZ)
            clashes = await find_conflicts(
                db,
                classroom_id=rule.classroom_id,
                teacher_id=rule.teacher_id,
                class_id=class_id,
                start_at=start_at,
                end_at=end_at,
            )
            if clashes:
                skipped_conflicts += 1
                conflicts.extend(clashes)
                continue
            lessons.append(
                Lesson(
                    tenant_id=tenant_id,
                    class_id=class_id,
                    classroom_id=rule.classroom_id,
                    teacher_id=rule.teacher_id,
                    start_at=start_at,
                    end_at=end_at,
                    status="normal",
                )
            )
            booked.add(start_at)
            generated += 1
        day += timedelta(days=1)

    if lessons:
        db.add_all(lessons)
        try:
            await db.flush()
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == "23P01":
                raise AppError(409, "SCHEDULE_CONFLICT", "该时段与已有课次冲突") from exc
            raise

    return GenerationResult(
        class_id=class_id,
        from_date=from_date,
        to_date=to_date,
        generated=generated,
        skipped_holidays=skipped_holidays,
        skipped_existing=skipped_existing,
        skipped_conflicts=skipped_conflicts,
        conflicts=conflicts,
    )


async def create_lesson(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    class_id: UUID,
    classroom_id: UUID,
    teacher_id: UUID,
    start_at: datetime,
    end_at: datetime,
) -> Lesson:
    """Insert a single lesson, rejecting any three-dimension clash with 409.

    Used by the concurrency test now and the 3.4 single-add endpoint later.
    The EXCLUDE constraints are the backstop: if the pre-check races and the
    INSERT still violates exclusion, ``23P01`` is translated to 409.
    """

    clashes = await find_conflicts(
        db,
        classroom_id=classroom_id,
        teacher_id=teacher_id,
        class_id=class_id,
        start_at=start_at,
        end_at=end_at,
    )
    if clashes:
        raise AppError(
            409,
            "SCHEDULE_CONFLICT",
            "该时段与已有课次冲突",
            detail={"conflicts": [clash.to_dict() for clash in clashes]},
        )

    lesson = Lesson(
        tenant_id=tenant_id,
        class_id=class_id,
        classroom_id=classroom_id,
        teacher_id=teacher_id,
        start_at=start_at,
        end_at=end_at,
        status="normal",
    )
    db.add(lesson)
    try:
        await db.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23P01":
            raise AppError(409, "SCHEDULE_CONFLICT", "该时段与已有课次冲突") from exc
        raise
    return lesson


def lesson_select():
    """Lessons joined with the display names the admin timetable needs."""

    return (
        select(Lesson, Class.name, Teacher.name, Classroom.name)
        .join(Class, Class.id == Lesson.class_id)
        .join(Teacher, Teacher.id == Lesson.teacher_id)
        .join(Classroom, Classroom.id == Lesson.classroom_id)
    )


async def list_lessons(
    db: AsyncSession,
    *,
    class_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Row], int]:
    stmt = lesson_select()
    count_stmt = select(func.count()).select_from(Lesson)
    if class_id is not None:
        stmt = stmt.where(Lesson.class_id == class_id)
        count_stmt = count_stmt.where(Lesson.class_id == class_id)
    if date_from is not None:
        stmt = stmt.where(Lesson.start_at >= day_start(date_from))
        count_stmt = count_stmt.where(Lesson.start_at >= day_start(date_from))
    if date_to is not None:
        upper = day_start(date_to + timedelta(days=1))
        stmt = stmt.where(Lesson.start_at < upper)
        count_stmt = count_stmt.where(Lesson.start_at < upper)

    total = int(await db.scalar(count_stmt) or 0)
    rows = await db.execute(
        stmt.order_by(Lesson.start_at, Lesson.id).offset((page - 1) * page_size).limit(page_size)
    )
    return list(rows), total
