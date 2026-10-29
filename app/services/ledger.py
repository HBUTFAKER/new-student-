"""Lesson packages, purchases and the append-only ledger (2.3 / 2.4).

Red line 2 lives here: no column ever stores a balance. Every number returned
by this module is derived from ``lesson_ledger`` with ``SUM(delta)``, and rows
are only ever inserted. ``deduct_lessons`` is the single place that decides
which package a consumed lesson comes out of, so the ordering rule from
``docs/phase0/04-er-diagram.md`` (earliest expiry first, then earliest purchase)
cannot drift between callers.
"""

import calendar
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.archive import Student
from app.models.course import Class, Course, CoursePackage
from app.models.ledger import LessonLedger
from app.models.schedule import Lesson
from app.models.trade import Purchase

# 业务日期按固定 UTC+8 计算：有效期是"哪一天到期"的业务概念，不跟服务器时区走。
BUSINESS_TZ = timezone(timedelta(hours=8))


def business_today() -> date:
    return datetime.now(BUSINESS_TZ).date()


def add_months(start: date, months: int) -> date:
    """Calendar-month arithmetic with day clamping (Jan 31 + 1 month = Feb 28)."""

    index = start.month - 1 + months
    year = start.year + index // 12
    month = index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def package_name(lesson_count: int, gift_lesson_count: int) -> str:
    if gift_lesson_count:
        return f"{lesson_count}次卡（赠{gift_lesson_count}）"
    return f"{lesson_count}次卡"


async def remaining_by_purchase(db: AsyncSession, student_id: UUID) -> dict[UUID, int]:
    """Per-package balance, straight from the ledger."""

    rows = await db.execute(
        select(LessonLedger.purchase_id, func.sum(LessonLedger.delta))
        .where(
            LessonLedger.student_id == student_id,
            LessonLedger.purchase_id.is_not(None),
        )
        .group_by(LessonLedger.purchase_id)
    )
    return {purchase_id: int(total or 0) for purchase_id, total in rows}


async def total_balance(db: AsyncSession, student_id: UUID) -> int:
    """Total balance = SUM(delta) over every ledger row of the student."""

    total = await db.scalar(
        select(func.coalesce(func.sum(LessonLedger.delta), 0)).where(
            LessonLedger.student_id == student_id
        )
    )
    return int(total or 0)


async def create_purchase(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    student_id: UUID,
    course_id: UUID,
    lesson_count: int,
    gift_lesson_count: int,
    valid_months: int,
    amount: Decimal,
    operator_id: UUID,
) -> tuple[Purchase, CoursePackage, list[LessonLedger]]:
    """Register an offline payment and grant the lessons in the same transaction.

    The package definition is snapshotted into ``course_package`` so that later
    edits to a template can never rewrite the history of this purchase.
    """

    student = await db.get(Student, student_id)
    if student is None:
        raise AppError(404, "STUDENT_NOT_FOUND", "学生不存在")
    if student.status != "active":
        raise AppError(422, "STUDENT_STATUS_INVALID", "学生不在读，不能缴费")
    course = await db.get(Course, course_id)
    if course is None:
        raise AppError(404, "COURSE_NOT_FOUND", "课程不存在")
    if course.status != "active":
        raise AppError(422, "COURSE_INACTIVE", "课程已停用，不能报名")
    if lesson_count < 1:
        raise AppError(422, "LESSON_COUNT_INVALID", "课时数必须为正整数")
    if valid_months < 1:
        raise AppError(422, "VALID_MONTHS_REQUIRED", "有效期必填（月）")
    if amount < 0:
        raise AppError(422, "AMOUNT_INVALID", "实收金额不能为负")

    purchased_at = datetime.now(UTC)
    expires_at = add_months(business_today(), valid_months)
    name = package_name(lesson_count, gift_lesson_count)

    package = CoursePackage(
        tenant_id=tenant_id,
        name=name,
        total_lessons=lesson_count,
        gift_lessons=gift_lesson_count,
        price=amount,
        valid_months=valid_months,
    )
    db.add(package)
    await db.flush()

    purchase = Purchase(
        tenant_id=tenant_id,
        student_id=student_id,
        course_id=course_id,
        package_id=package.id,
        quantity=1,
        amount=amount,
        purchased_at=purchased_at,
        expires_at=expires_at,
    )
    db.add(purchase)
    await db.flush()

    entries = [
        LessonLedger(
            tenant_id=tenant_id,
            student_id=student_id,
            purchase_id=purchase.id,
            lesson_id=None,
            type="purchase",
            delta=lesson_count,
            reason=f"购买 {course.name} {lesson_count} 课时",
            operator_id=operator_id,
        )
    ]
    if gift_lesson_count > 0:
        # 赠课单独一条 grant 流水，不与购买混在一条（2.3 要求）。
        entries.append(
            LessonLedger(
                tenant_id=tenant_id,
                student_id=student_id,
                purchase_id=purchase.id,
                lesson_id=None,
                type="grant",
                delta=gift_lesson_count,
                reason=f"赠送 {gift_lesson_count} 课时",
                operator_id=operator_id,
            )
        )
    db.add_all(entries)
    await db.flush()
    return purchase, package, entries


async def deduct_lessons(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    student_id: UUID,
    lesson_id: UUID,
    count: int,
    operator_id: UUID,
    today: date | None = None,
) -> list[LessonLedger]:
    """Consume ``count`` lessons, newest-expiry-last, in one atomic step.

    The purchase rows of the student (still valid ones) are locked with
    ``SELECT ... FOR UPDATE`` before the per-package balances are read, so two
    concurrent check-ins cannot both spend the same remaining lesson. A package
    that cannot cover the whole request is drained and the rest spills into the
    next package as a separate ``consume`` row. Insufficient total balance
    raises before anything is written, so no partial deduction survives.

    Only the lesson row is validated here: whether the student is enrolled in
    that lesson's class is the check-in step's business (2.5), which is the only
    production caller. Callers must run inside a tenant-scoped transaction.
    """

    if count <= 0:
        raise AppError(422, "LESSON_COUNT_INVALID", "扣减课时必须为正整数")
    lesson = await db.get(Lesson, lesson_id)
    if lesson is None:
        raise AppError(404, "LESSON_NOT_FOUND", "课次不存在")

    today = today or business_today()
    purchases = (
        await db.scalars(
            select(Purchase)
            .where(
                Purchase.student_id == student_id,
                or_(Purchase.expires_at.is_(None), Purchase.expires_at >= today),
            )
            .order_by(
                Purchase.expires_at.asc().nulls_last(),
                Purchase.purchased_at.asc(),
                Purchase.id.asc(),
            )
            .with_for_update()
        )
    ).all()

    remaining = await remaining_by_purchase(db, student_id)
    needed = count
    entries: list[LessonLedger] = []
    for purchase in purchases:
        if needed == 0:
            break
        left = remaining.get(purchase.id, 0)
        if left <= 0:
            continue
        take = min(left, needed)
        entries.append(
            LessonLedger(
                tenant_id=tenant_id,
                student_id=student_id,
                purchase_id=purchase.id,
                lesson_id=lesson_id,
                type="consume",
                delta=-take,
                reason=None,
                operator_id=operator_id,
            )
        )
        needed -= take

    if needed > 0:
        raise AppError(
            409,
            "BALANCE_EXHAUSTED",
            f"课时余额不足，还差 {needed} 课时",
        )

    db.add_all(entries)
    await db.flush()
    return entries


async def adjust_lessons(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    student_id: UUID,
    purchase_id: UUID,
    delta: int,
    reason: str | None,
    operator_id: UUID,
) -> LessonLedger:
    """Manual adjustment (US-209): reason mandatory, row is never edited."""

    cleaned = (reason or "").strip()
    if not cleaned:
        raise AppError(422, "REASON_REQUIRED", "课时调整必须填写原因")
    if delta == 0:
        raise AppError(422, "DELTA_INVALID", "调整数量不能为 0")

    student = await db.get(Student, student_id)
    if student is None:
        raise AppError(404, "STUDENT_NOT_FOUND", "学生不存在")
    purchase = await db.scalar(
        select(Purchase)
        .where(Purchase.id == purchase_id, Purchase.student_id == student_id)
        .with_for_update()
    )
    if purchase is None:
        raise AppError(404, "PURCHASE_NOT_FOUND", "课时包不存在")

    total = await total_balance(db, student_id)
    if total + delta < 0:
        raise AppError(
            409,
            "INSUFFICIENT_BALANCE",
            f"调整后余额为负（当前 {total}，调整 {delta}）",
        )

    entry = LessonLedger(
        tenant_id=tenant_id,
        student_id=student_id,
        purchase_id=purchase_id,
        lesson_id=None,
        type="adjust",
        delta=delta,
        reason=cleaned,
        operator_id=operator_id,
    )
    db.add(entry)
    await db.flush()
    return entry


async def student_packages(
    db: AsyncSession, student_id: UUID, *, today: date | None = None
) -> list[dict]:
    """Live package view: granted / remaining / expiry / status, never stored."""

    today = today or business_today()
    sums = (
        select(
            LessonLedger.purchase_id.label("purchase_id"),
            func.sum(LessonLedger.delta).label("remaining"),
        )
        .where(
            LessonLedger.student_id == student_id,
            LessonLedger.purchase_id.is_not(None),
        )
        .group_by(LessonLedger.purchase_id)
        .subquery()
    )
    rows = await db.execute(
        select(
            Purchase,
            Course.name,
            CoursePackage.name,
            CoursePackage.total_lessons,
            CoursePackage.gift_lessons,
            func.coalesce(sums.c.remaining, 0),
        )
        .join(CoursePackage, CoursePackage.id == Purchase.package_id)
        .join(Course, Course.id == Purchase.course_id)
        .outerjoin(sums, sums.c.purchase_id == Purchase.id)
        .where(Purchase.student_id == student_id)
        .order_by(
            Purchase.expires_at.asc().nulls_last(),
            Purchase.purchased_at.asc(),
            Purchase.id.asc(),
        )
    )
    packages: list[dict] = []
    for purchase, course_name, name, total_lessons, gift_lessons, remaining in rows:
        left = int(remaining or 0)
        if left <= 0:
            status = "exhausted"
        elif purchase.expires_at is not None and purchase.expires_at < today:
            status = "expired"
        else:
            status = "active"
        packages.append(
            {
                "purchase_id": purchase.id,
                "course_id": purchase.course_id,
                "course_name": course_name,
                "package_name": name,
                "granted": total_lessons + gift_lessons,
                "remaining": left,
                "purchased_at": purchase.purchased_at,
                "expires_at": purchase.expires_at,
                "status": status,
            }
        )
    return packages


def ledger_query(student_id: UUID):
    """Ledger entries with the class name of the lesson they were spent on."""

    return (
        select(LessonLedger, Class.name)
        .outerjoin(Lesson, Lesson.id == LessonLedger.lesson_id)
        .outerjoin(Class, Class.id == Lesson.class_id)
        .where(LessonLedger.student_id == student_id)
    )
