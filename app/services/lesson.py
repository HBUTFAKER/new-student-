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
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.archive import Classroom, Teacher
from app.models.attendance import Attendance
from app.models.course import Class
from app.models.schedule import Lesson, ScheduleRule
from app.services.conflict import Conflict, find_conflicts
from app.services.holiday import holiday_dates
from app.services.ledger import BUSINESS_TZ, business_today

# 单次加课时，两个事务若同时通过预检并插同一时段，EXCLUDE 约束会走 Postgres 的
# speculative insertion：双方互相等待对方的 token，`40P01`（deadlock）或 `40001`
# （serialization failure）就会代替预期中的 `23P01` 冒出来，变成 500。
# 这两类错误可以在 SAVEPOINT 回滚后重试：赢家提交后，重试的 INSERT 会看到已存在
# 行，稳定落成 `23P01` → 409。重试次数取 3 只为兜住「三方同时抢」的极端情形。
_RETRYABLE_INSERT_SQLSTATES = frozenset({"40P01", "40001"})
_INSERT_ATTEMPTS = 3


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


def _require_aware_time(start_at: datetime, end_at: datetime) -> None:
    if (
        start_at.tzinfo is None
        or start_at.utcoffset() is None
        or end_at.tzinfo is None
        or end_at.utcoffset() is None
    ):
        raise AppError(422, "TIME_NAIVE", "时间必须带时区")


async def _ensure_lesson_refs(
    db: AsyncSession, class_id: UUID, classroom_id: UUID, teacher_id: UUID
) -> None:
    klass = await db.get(Class, class_id)
    if klass is None:
        raise AppError(404, "CLASS_NOT_FOUND", "班级不存在")
    if klass.status == "ended":
        raise AppError(409, "CLASS_ENDED", "班级已结课，不能再加课")
    if await db.get(Classroom, classroom_id) is None:
        raise AppError(404, "CLASSROOM_NOT_FOUND", "教室不存在")
    teacher = await db.get(Teacher, teacher_id)
    if teacher is None:
        raise AppError(404, "TEACHER_NOT_FOUND", "老师不存在")
    if teacher.status != "active":
        raise AppError(422, "TEACHER_INACTIVE", "老师已停用，不能排课")


def _schedule_conflict(clashes: list[Conflict]) -> AppError:
    """409 SCHEDULE_CONFLICT，带上冲突明细（管理端靠 ``detail.conflicts`` 弹窗）。"""

    return AppError(
        409,
        "SCHEDULE_CONFLICT",
        "该时段与已有课次冲突",
        detail={"conflicts": [clash.to_dict() for clash in clashes]},
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

    Used by the 3.4 single-add endpoint and the concurrency test.
    The EXCLUDE constraints are the backstop: if the pre-check races and the
    INSERT still violates exclusion, ``23P01`` is translated to 409.

    The race window is not only "one wins, one violates": two transactions
    inserting the same slot can deadlock on each other's speculative-insertion
    token (``40P01`` / ``40001``). That is retried inside a savepoint so the
    caller still gets a clean 409 instead of a 500 — see the constants above.
    """

    _require_aware_time(start_at, end_at)
    if start_at >= end_at:
        raise AppError(422, "TIME_RANGE_INVALID", "结束时间必须晚于开始时间")
    await _ensure_lesson_refs(db, class_id, classroom_id, teacher_id)

    clashes = await find_conflicts(
        db,
        classroom_id=classroom_id,
        teacher_id=teacher_id,
        class_id=class_id,
        start_at=start_at,
        end_at=end_at,
    )
    if clashes:
        raise _schedule_conflict(clashes)

    for attempt in range(1, _INSERT_ATTEMPTS + 1):
        lesson = Lesson(
            tenant_id=tenant_id,
            class_id=class_id,
            classroom_id=classroom_id,
            teacher_id=teacher_id,
            start_at=start_at,
            end_at=end_at,
            status="normal",
        )
        try:
            # SAVEPOINT：INSERT 失败（含死锁）时只回滚这一段，外层事务还能继续
            # 用同一个连接重试。对象建在 savepoint 里面，回滚后不会残留成待插入。
            async with db.begin_nested():
                db.add(lesson)
                await db.flush()
        except DBAPIError as exc:
            # 死锁抛的是裸 DBAPIError（不是 IntegrityError），所以这里按 sqlstate 分流。
            sqlstate = getattr(exc.orig, "sqlstate", None)
            if sqlstate == "23P01":
                # 预检被并发绕过后才走到这里：补一次明细查询，管理端弹窗要 detail.conflicts。
                # 查询本身若也失败（连接已不可用），退回不带明细的 409，别拿 500 顶掉 409。
                try:
                    late_clashes = await find_conflicts(
                        db,
                        classroom_id=classroom_id,
                        teacher_id=teacher_id,
                        class_id=class_id,
                        start_at=start_at,
                        end_at=end_at,
                    )
                except DBAPIError:
                    late_clashes = []
                raise _schedule_conflict(late_clashes) from exc
            if sqlstate in _RETRYABLE_INSERT_SQLSTATES and attempt < _INSERT_ATTEMPTS:
                continue
            raise
        return lesson

    raise AssertionError("unreachable: insert attempts exhausted without raising")


async def _ensure_adjustable(db: AsyncSession, lesson: Lesson) -> None:
    """已签到或已开始的课次不能再调整（调课与取消统一守门）。"""

    attended = await db.scalar(select(Attendance.id).where(Attendance.lesson_id == lesson.id))
    if attended is not None or lesson.start_at < datetime.now(BUSINESS_TZ):
        raise AppError(422, "LESSON_ALREADY_DONE", "已签到或已开始的课次不能再调整")


async def update_lesson(db: AsyncSession, *, lesson_id: UUID, data: dict) -> Lesson:
    """Move or cancel a lesson (3.4). Neither path writes any ledger row.

    Cancellation only flips ``status`` to ``cancelled`` (soft), which the EXCLUDE
    predicate ignores, so the slot is released. A move runs the same three-
    dimension pre-check as insertion — excluding the lesson itself — plus the
    EXCLUDE backstop, and marks the lesson ``moved``.
    """

    lesson = await db.get(Lesson, lesson_id)
    if lesson is None:
        raise AppError(404, "LESSON_NOT_FOUND", "课次不存在")

    await _ensure_adjustable(db, lesson)

    if data.get("status") == "cancelled":
        lesson.status = "cancelled"
        await db.flush()
        return lesson

    for key in ("start_at", "end_at", "classroom_id", "teacher_id"):
        if key in data and data[key] is None:
            raise AppError(422, "VALIDATION_ERROR", f"{key} 不能为空")
    if not any(key in data for key in ("start_at", "end_at", "classroom_id", "teacher_id")):
        raise AppError(
            422, "LESSON_UPDATE_EMPTY", "调课需提供至少一个变更字段，或 status=cancelled"
        )

    new_classroom = data.get("classroom_id", lesson.classroom_id)
    new_teacher = data.get("teacher_id", lesson.teacher_id)
    new_start = data.get("start_at", lesson.start_at)
    new_end = data.get("end_at", lesson.end_at)
    _require_aware_time(new_start, new_end)
    if new_start >= new_end:
        raise AppError(422, "TIME_RANGE_INVALID", "结束时间必须晚于开始时间")

    if await db.get(Classroom, new_classroom) is None:
        raise AppError(404, "CLASSROOM_NOT_FOUND", "教室不存在")
    teacher = await db.get(Teacher, new_teacher)
    if teacher is None:
        raise AppError(404, "TEACHER_NOT_FOUND", "老师不存在")
    if teacher.status != "active":
        raise AppError(422, "TEACHER_INACTIVE", "老师已停用，不能排课")

    clashes = [
        clash
        for clash in await find_conflicts(
            db,
            classroom_id=new_classroom,
            teacher_id=new_teacher,
            class_id=lesson.class_id,
            start_at=new_start,
            end_at=new_end,
        )
        if clash.lesson_id != lesson.id
    ]
    if clashes:
        raise AppError(
            409,
            "LESSON_OVERLAP",
            "该时段与已有课次冲突",
            detail={"conflicts": [clash.to_dict() for clash in clashes]},
        )

    lesson.classroom_id = new_classroom
    lesson.teacher_id = new_teacher
    lesson.start_at = new_start
    lesson.end_at = new_end
    lesson.status = "moved"
    try:
        await db.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23P01":
            raise AppError(409, "LESSON_OVERLAP", "该时段与已有课次冲突") from exc
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
