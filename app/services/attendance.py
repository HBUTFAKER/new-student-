"""Attendance: check-in with lesson deduction (3.5) and makeup scheduling (3.4).

**3.5 是全项目第一红线。** 扣错课时不报错、静默发生，所以这里的每一步都按
「同事务 + 逐学生 SAVEPOINT + 唯一键判幂等」来写：

* 扣课只调 ``app.services.ledger.deduct_lessons``（先过期先扣、拆包、行锁都在
  那里），本模块不重写扣包逻辑，也不写任何 grant/refund/adjust 流水。
* 整批请求在调用方的那个事务里，每个学生额外包一层 SAVEPOINT：某个学生撞唯一
  键或余额不足只回滚他自己的 savepoint，别人的结果不受污染。
* 幂等由 ``attendance`` 的唯一键 ``(tenant_id, lesson_id, student_id)`` 裁决，
  不做「先 SELECT 再 INSERT」——那在并发下是假的。
* 唯一例外是 3.4 预登记的 ``status='makeup'`` 行：那不是重复提交，是补课学生
  正式签到，原地转正并扣课；转正后即为最终状态，再提交走 deduplicated。
* 扣课失败（``BALANCE_EXHAUSTED``）时 savepoint 一并回滚，attendance 行不留，
  所以购课后可以对该学生单独重提，已成功的别人不会被重扣。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.archive import Student
from app.models.attendance import Attendance
from app.models.course import Class
from app.models.schedule import Lesson
from app.services.ledger import BUSINESS_TZ, deduct_lessons
from app.services.org_config import get_absence_rule

# 缺勤/请假才算"缺"，present 与 makeup 都不构成补课前提。
ABSENT_STATUSES = frozenset({"absent", "leave"})

# 签到写下的三种状态；makeup 是 3.4 的预登记，不是最终结果。
FINAL_STATUSES = frozenset({"present", "leave", "absent"})

UNIQUE_VIOLATION = "23505"


@dataclass(frozen=True, slots=True)
class CheckInRequest:
    student_id: UUID
    status: str


@dataclass(frozen=True, slots=True)
class CheckInResult:
    student_id: UUID
    status: str
    deduct_lessons: int
    deduplicated: bool
    # 仅 failed 时出现：目前只有 BALANCE_EXHAUSTED。
    reason: str | None = None


class _SeatTaken(Exception):
    """Raised inside a savepoint when the attendance unique key is hit.

    It must escape the ``begin_nested()`` block so the savepoint rolls back;
    catching ``IntegrityError`` on the spot would leave the savepoint poisoned.
    """


def _sqlstate(exc: IntegrityError) -> str | None:
    return getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)


def _deduct_count(status: str, rule: str) -> int:
    """present 扣 1；leave/absent 按机构规则（none → 0，deduct → 1）。"""

    if status == "present":
        return 1
    return 1 if rule == "deduct" else 0


async def _lesson_for_write(db: AsyncSession, lesson_id: UUID, teacher_id: UUID | None) -> Lesson:
    lesson = await db.get(Lesson, lesson_id)
    if lesson is None:
        raise AppError(404, "LESSON_NOT_FOUND", "课次不存在")
    await _ensure_scope(db, lesson, teacher_id)
    if lesson.status == "cancelled":
        raise AppError(422, "LESSON_CANCELLED", "已取消的课次不能签到")
    if datetime.now(BUSINESS_TZ) < lesson.start_at:
        raise AppError(422, "LESSON_NOT_STARTED", "课次尚未开始，不能签到")
    return lesson


async def _ensure_scope(db: AsyncSession, lesson: Lesson, teacher_id: UUID | None) -> None:
    """0.2 矩阵：教务/管理员任意课次，老师仅任教班级（否则 403）。"""

    if teacher_id is None:
        return
    class_teacher = await db.scalar(select(Class.teacher_id).where(Class.id == lesson.class_id))
    if class_teacher != teacher_id:
        raise AppError(403, "OUT_OF_SCOPE", "只能给自己任教的班级签到")


async def check_in(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    lesson_id: UUID,
    records: Sequence[CheckInRequest],
    operator_id: UUID,
    teacher_id: UUID | None = None,
) -> list[CheckInResult]:
    """Mark one lesson's roll call, deducting lessons in the same transaction.

    ``teacher_id`` is the authenticated teacher's id for the teacher role and
    ``None`` for admin/registrar; it only narrows the scope, never the rules.
    """

    await _lesson_for_write(db, lesson_id, teacher_id)
    rule = await get_absence_rule(db)

    student_ids = {record.student_id for record in records}
    if student_ids:
        # RLS 让别家学生查不到 → 404，顺带挡掉跨租户写入。
        known = set((await db.scalars(select(Student.id).where(Student.id.in_(student_ids)))).all())
        if known != student_ids:
            raise AppError(404, "STUDENT_NOT_FOUND", "学生不存在")

    results: list[CheckInResult] = []
    for record in records:
        results.append(
            await _check_in_one(
                db,
                tenant_id=tenant_id,
                lesson_id=lesson_id,
                student_id=record.student_id,
                status=record.status,
                deduct_count=_deduct_count(record.status, rule),
                operator_id=operator_id,
            )
        )
    return results


async def _check_in_one(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    lesson_id: UUID,
    student_id: UUID,
    status: str,
    deduct_count: int,
    operator_id: UUID,
) -> CheckInResult:
    for _ in range(2):
        try:
            return await _insert_seat(
                db,
                tenant_id=tenant_id,
                lesson_id=lesson_id,
                student_id=student_id,
                status=status,
                deduct_count=deduct_count,
                operator_id=operator_id,
            )
        except _SeatTaken:
            existing = await _resolve_existing(
                db,
                tenant_id=tenant_id,
                lesson_id=lesson_id,
                student_id=student_id,
                status=status,
                deduct_count=deduct_count,
                operator_id=operator_id,
            )
            if existing is not None:
                return existing
            # 抢到唯一键的那个事务回滚了，位置空出来 → 再试一次。
    raise AppError(409, "CHECKIN_CONFLICT", "签到并发冲突，请重试")


async def _insert_seat(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    lesson_id: UUID,
    student_id: UUID,
    status: str,
    deduct_count: int,
    operator_id: UUID,
) -> CheckInResult:
    """Insert the attendance row and deduct, or roll the whole savepoint back."""

    try:
        async with db.begin_nested():
            db.add(
                Attendance(
                    tenant_id=tenant_id,
                    lesson_id=lesson_id,
                    student_id=student_id,
                    status=status,
                    deduct_lessons=deduct_count,
                    operator_id=operator_id,
                )
            )
            try:
                await db.flush()
            except IntegrityError as exc:
                if _sqlstate(exc) == UNIQUE_VIOLATION:
                    raise _SeatTaken from exc
                raise
            if deduct_count:
                await deduct_lessons(
                    db,
                    tenant_id=tenant_id,
                    student_id=student_id,
                    lesson_id=lesson_id,
                    count=deduct_count,
                    operator_id=operator_id,
                )
            return CheckInResult(
                student_id=student_id,
                status=status,
                deduct_lessons=deduct_count,
                deduplicated=False,
            )
    except AppError as exc:
        if exc.code == "BALANCE_EXHAUSTED":
            # savepoint 已回滚 → attendance 行没留下，购课后可单独重提。
            return CheckInResult(
                student_id=student_id,
                status="failed",
                deduct_lessons=0,
                deduplicated=False,
                reason="BALANCE_EXHAUSTED",
            )
        raise


async def _resolve_existing(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    lesson_id: UUID,
    student_id: UUID,
    status: str,
    deduct_count: int,
    operator_id: UUID,
) -> CheckInResult | None:
    """Re-read the row that won the unique key and decide what it means.

    Final status → nothing to do, ``deduplicated``. ``makeup`` → this is the
    makeup student checking in for real: promote the row in place and deduct.
    Row is locked so two concurrent promotions cannot both deduct.
    """

    try:
        async with db.begin_nested():
            seat = await db.scalar(
                select(Attendance)
                .where(
                    Attendance.lesson_id == lesson_id,
                    Attendance.student_id == student_id,
                )
                .with_for_update()
            )
            if seat is None:
                return None
            if seat.status in FINAL_STATUSES:
                return CheckInResult(
                    student_id=student_id,
                    status=seat.status,
                    deduct_lessons=seat.deduct_lessons,
                    deduplicated=True,
                )
            seat.status = status
            seat.deduct_lessons = deduct_count
            seat.operator_id = operator_id
            if deduct_count:
                await deduct_lessons(
                    db,
                    tenant_id=tenant_id,
                    student_id=student_id,
                    lesson_id=lesson_id,
                    count=deduct_count,
                    operator_id=operator_id,
                )
            await db.flush()
            return CheckInResult(
                student_id=student_id,
                status=status,
                deduct_lessons=deduct_count,
                deduplicated=False,
            )
    except AppError as exc:
        if exc.code == "BALANCE_EXHAUSTED":
            return CheckInResult(
                student_id=student_id,
                status="failed",
                deduct_lessons=0,
                deduplicated=False,
                reason="BALANCE_EXHAUSTED",
            )
        raise


async def list_attendance(
    db: AsyncSession, *, lesson_id: UUID, teacher_id: UUID | None = None
) -> list[Attendance]:
    """Roll-call result for one lesson; reading does not require it to have started."""

    lesson = await db.get(Lesson, lesson_id)
    if lesson is None:
        raise AppError(404, "LESSON_NOT_FOUND", "课次不存在")
    await _ensure_scope(db, lesson, teacher_id)
    rows = await db.scalars(
        select(Attendance)
        .where(Attendance.lesson_id == lesson.id)
        .order_by(Attendance.created_at, Attendance.id)
    )
    return list(rows.all())


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
