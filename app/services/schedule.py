"""Schedule rule business logic (3.1).

Scope note: this module only persists the recurrence pattern. Turning a rule
into concrete ``lesson`` rows is step 3.2, and teacher/classroom/student
overlap detection is step 3.3 — neither is done here.

Field rules come from ``docs/phase0/04-data-dictionary.md``: one row per
weekday, ``end_date`` nullable, no ``weeks`` column. Every write is scoped by
the tenant GUC set in ``app.db.tenant``, so a foreign row simply does not exist
for the current tenant (404, never 403).
"""

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import Row, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.archive import Classroom, Teacher
from app.models.course import Class
from app.models.schedule import ScheduleRule
from app.services.conflict import Conflict, find_conflicts
from app.services.ledger import BUSINESS_TZ


def ensure_time_range(start_time: time, end_time: time) -> None:
    if start_time >= end_time:
        raise AppError(422, "TIME_RANGE_INVALID", "结束时间必须晚于开始时间")


def ensure_date_range(start_date: date, end_date: date | None) -> None:
    if end_date is not None and end_date < start_date:
        raise AppError(422, "DATE_RANGE_INVALID", "结束日期不能早于开始日期")


async def _get_class(db: AsyncSession, class_id: UUID) -> Class:
    klass = await db.get(Class, class_id)
    if klass is None:
        raise AppError(404, "CLASS_NOT_FOUND", "班级不存在")
    return klass


async def _get_classroom(db: AsyncSession, classroom_id: UUID) -> Classroom:
    classroom = await db.get(Classroom, classroom_id)
    if classroom is None:
        raise AppError(404, "CLASSROOM_NOT_FOUND", "教室不存在")
    return classroom


async def _get_teacher(db: AsyncSession, teacher_id: UUID) -> Teacher:
    teacher = await db.get(Teacher, teacher_id)
    if teacher is None:
        raise AppError(404, "TEACHER_NOT_FOUND", "老师不存在")
    if teacher.status != "active":
        raise AppError(422, "TEACHER_INACTIVE", "老师已停用，不能排课")
    return teacher


def rule_select():
    """Rules joined with the display names the admin UI needs."""

    return (
        select(ScheduleRule, Class.name, Teacher.name, Classroom.name)
        .join(Class, Class.id == ScheduleRule.class_id)
        .join(Teacher, Teacher.id == ScheduleRule.teacher_id)
        .join(Classroom, Classroom.id == ScheduleRule.classroom_id)
    )


async def create_rules(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    class_id: UUID,
    classroom_id: UUID,
    teacher_id: UUID,
    weekdays: Sequence[int],
    start_time: time,
    end_time: time,
    start_date: date,
    end_date: date | None,
) -> list[ScheduleRule]:
    """Write one row per weekday in a single flush (all-or-nothing)."""

    if not weekdays:
        raise AppError(422, "WEEKDAY_REQUIRED", "至少需要一个 weekday")
    if len(set(weekdays)) != len(weekdays):
        raise AppError(422, "WEEKDAY_DUPLICATED", "weekday 不能重复")
    if any(day < 0 or day > 6 for day in weekdays):
        raise AppError(422, "WEEKDAY_INVALID", "weekday 取值必须是 0-6")
    ensure_time_range(start_time, end_time)
    ensure_date_range(start_date, end_date)

    klass = await _get_class(db, class_id)
    if klass.status == "ended":
        raise AppError(409, "CLASS_ENDED", "班级已结课，不能再排课")
    await _get_classroom(db, classroom_id)
    await _get_teacher(db, teacher_id)

    rules = [
        ScheduleRule(
            tenant_id=tenant_id,
            class_id=class_id,
            classroom_id=classroom_id,
            teacher_id=teacher_id,
            weekday=weekday,
            start_time=start_time,
            end_time=end_time,
            start_date=start_date,
            end_date=end_date,
        )
        for weekday in weekdays
    ]
    db.add_all(rules)
    await db.flush()
    return rules


async def get_rule(db: AsyncSession, rule_id: UUID) -> ScheduleRule:
    rule = await db.get(ScheduleRule, rule_id)
    if rule is None:
        raise AppError(404, "SCHEDULE_RULE_NOT_FOUND", "排课规律不存在")
    return rule


async def list_rules(
    db: AsyncSession,
    *,
    class_id: UUID | None = None,
    teacher_id: UUID | None = None,
    classroom_id: UUID | None = None,
    weekday: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Row], int]:
    filters = [
        (ScheduleRule.class_id, class_id),
        (ScheduleRule.teacher_id, teacher_id),
        (ScheduleRule.classroom_id, classroom_id),
        (ScheduleRule.weekday, weekday),
    ]
    stmt = rule_select()
    count_stmt = select(func.count()).select_from(ScheduleRule)
    for column, value in filters:
        if value is not None:
            stmt = stmt.where(column == value)
            count_stmt = count_stmt.where(column == value)

    total = int(await db.scalar(count_stmt) or 0)
    rows = await db.execute(
        stmt.order_by(ScheduleRule.weekday, ScheduleRule.start_time, ScheduleRule.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(rows), total


async def update_rule(db: AsyncSession, rule_id: UUID, data: dict) -> ScheduleRule:
    rule = await get_rule(db, rule_id)

    for field in ("classroom_id", "teacher_id", "weekday", "start_time", "end_time", "start_date"):
        if field in data and data[field] is None:
            raise AppError(422, "VALIDATION_ERROR", f"{field} 不能为空")

    start_time = data.get("start_time", rule.start_time)
    end_time = data.get("end_time", rule.end_time)
    start_date = data.get("start_date", rule.start_date)
    end_date = data["end_date"] if "end_date" in data else rule.end_date
    ensure_time_range(start_time, end_time)
    ensure_date_range(start_date, end_date)

    if "classroom_id" in data:
        await _get_classroom(db, data["classroom_id"])
    if "teacher_id" in data:
        await _get_teacher(db, data["teacher_id"])

    for field, value in data.items():
        setattr(rule, field, value)
    await db.flush()
    return rule


async def delete_rule(db: AsyncSession, rule_id: UUID) -> None:
    """``schedule_rule`` 没有 status 列，字典的"改状态不删"口径只适用于档案表。"""

    rule = await get_rule(db, rule_id)
    await db.delete(rule)
    await db.flush()


async def validate_rule(
    db: AsyncSession,
    *,
    class_id: UUID,
    classroom_id: UUID,
    teacher_id: UUID,
    weekdays: Sequence[int],
    start_time: time,
    end_time: time,
    start_date: date,
    end_date: date,
) -> list[Conflict]:
    """3.3 预检：把规律展开成具体时段，跑三维度冲突检测，不落库。

    与生成共用同一套校验（引用存在、班级未结课、老师在职）与同一套冲突
    检测，但不写任何行。同一条已占用课次只报一次（按 lesson_id 去重）。
    """

    klass = await _get_class(db, class_id)
    if klass.status == "ended":
        raise AppError(409, "CLASS_ENDED", "班级已结课，不能再排课")
    await _get_classroom(db, classroom_id)
    await _get_teacher(db, teacher_id)

    conflicts: list[Conflict] = []
    seen: set[UUID] = set()
    day = start_date
    while day <= end_date:
        if day.weekday() in weekdays:
            start_at = datetime.combine(day, start_time, tzinfo=BUSINESS_TZ)
            end_at = datetime.combine(day, end_time, tzinfo=BUSINESS_TZ)
            for clash in await find_conflicts(
                db,
                classroom_id=classroom_id,
                teacher_id=teacher_id,
                class_id=class_id,
                start_at=start_at,
                end_at=end_at,
            ):
                if clash.lesson_id not in seen:
                    seen.add(clash.lesson_id)
                    conflicts.append(clash)
        day += timedelta(days=1)
    return conflicts
