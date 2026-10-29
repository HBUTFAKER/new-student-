"""Refund, class transfer and make-up lessons (step 2.5).

All three share the 2.3/2.4 rule: only the ledger may change a balance. A
refund writes a single ``refund`` row pointing at the original purchase (the
unchanged rest of the package stays untouched -- no implicit zeroing). A
transfer moves the roster row only and never writes the ledger. A make-up
writes one ``grant`` row whose ``lesson_id`` is the *missed* lesson; since
every other ``grant`` row has ``lesson_id = NULL``, that column doubles as the
"one make-up per absence" idempotency check without a new table.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.archive import Student
from app.models.attendance import Attendance
from app.models.course import Class, ClassEnrollment
from app.models.ledger import LessonLedger
from app.models.schedule import Lesson
from app.models.trade import Purchase
from app.services.ledger import business_today, remaining_by_purchase


async def _get_student_or_404(db: AsyncSession, student_id: UUID) -> Student:
    student = await db.get(Student, student_id)
    if student is None:
        raise AppError(404, "STUDENT_NOT_FOUND", "学生不存在")
    return student


async def refund_lessons(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    student_id: UUID,
    purchase_id: UUID,
    refund_lessons: int,
    refund_amount: Decimal,
    reason: str | None,
    operator_id: UUID,
) -> LessonLedger:
    """Refund lessons against one purchase: a single negative ``refund`` row.

    The purchase row is locked so two concurrent refunds cannot both read the
    same remaining balance and drive it negative. Only ``refund_lessons``
    lessons leave the balance; the untouched rest of the package is the
    institution's business, not the ledger's.
    """

    cleaned = (reason or "").strip()
    if not cleaned:
        raise AppError(422, "REASON_REQUIRED", "退费必须填写原因")
    if refund_lessons < 1:
        raise AppError(422, "REFUND_LESSONS_INVALID", "退费课时必须为正整数")
    if refund_amount < 0:
        raise AppError(422, "AMOUNT_INVALID", "退费金额不能为负")

    await _get_student_or_404(db, student_id)
    purchase = await db.scalar(
        select(Purchase)
        .where(Purchase.id == purchase_id, Purchase.student_id == student_id)
        .with_for_update()
    )
    if purchase is None:
        raise AppError(404, "PURCHASE_NOT_FOUND", "课时包不存在")

    remaining = (await remaining_by_purchase(db, student_id)).get(purchase.id, 0)
    if refund_lessons > remaining:
        raise AppError(
            409,
            "REFUND_EXCEEDS_PACKAGE",
            f"退费课时超过该包剩余（剩余 {remaining}，退 {refund_lessons}）",
        )

    entry = LessonLedger(
        tenant_id=tenant_id,
        student_id=student_id,
        purchase_id=purchase.id,
        lesson_id=None,
        type="refund",
        delta=-refund_lessons,
        reason=f"退费 ¥{refund_amount}：{cleaned}",
        operator_id=operator_id,
    )
    db.add(entry)
    await db.flush()
    return entry


async def transfer_student(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    student_id: UUID,
    from_class_id: UUID,
    to_class_id: UUID,
    operator_id: UUID,
) -> tuple[Class, Class]:
    """Move a student between two classes of the *same course*, roster only.

    No ledger row is written: a transfer changes where the remaining lessons
    are consumed, not how many exist. Crossing courses would silently make the
    remaining lessons of the old course unusable, so it is rejected here
    (422 ``TRANSFER_COURSE_MISMATCH``); the documented path is refund + a new
    purchase. The destination class row is locked to serialise concurrent
    transfers against the capacity check.
    """

    student = await _get_student_or_404(db, student_id)
    from_class = await db.get(Class, from_class_id)
    if from_class is None:
        raise AppError(404, "CLASS_NOT_FOUND", "转出班级不存在")
    to_class = await db.get(Class, to_class_id, with_for_update=True)
    if to_class is None:
        raise AppError(404, "CLASS_NOT_FOUND", "转入班级不存在")

    if from_class.course_id != to_class.course_id:
        raise AppError(
            422,
            "TRANSFER_COURSE_MISMATCH",
            "两个班级课程不同，课时不可复用；请先退费再购买目标课程",
        )
    if to_class.status == "ended" or (
        to_class.term_end is not None and to_class.term_end < date.today()
    ):
        raise AppError(409, "CLASS_ENDED", "转入班级已结课")

    if student.status != "active":
        raise AppError(422, "STUDENT_STATUS_INVALID", "学生不在读，不能转班")

    source = await db.scalar(
        select(ClassEnrollment).where(
            ClassEnrollment.class_id == from_class_id,
            ClassEnrollment.student_id == student_id,
        )
    )
    if source is None:
        raise AppError(404, "ENROLLMENT_NOT_FOUND", "学生不在转出班级")

    duplicated = await db.scalar(
        select(ClassEnrollment.id).where(
            ClassEnrollment.class_id == to_class_id,
            ClassEnrollment.student_id == student_id,
        )
    )
    if duplicated is not None:
        raise AppError(409, "ALREADY_ENROLLED", "学生已在转入班级")

    enrolled = int(
        await db.scalar(
            select(func.count())
            .select_from(ClassEnrollment)
            .where(ClassEnrollment.class_id == to_class_id)
        )
        or 0
    )
    if to_class.capacity is not None and enrolled >= to_class.capacity:
        raise AppError(409, "CLASS_FULL", f"转入班级已满（容量 {to_class.capacity}）")

    await db.delete(source)
    db.add(
        ClassEnrollment(
            tenant_id=tenant_id,
            class_id=to_class_id,
            student_id=student_id,
            joined_at=date.today(),
        )
    )
    await db.flush()
    return from_class, to_class


async def create_makeup(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    lesson_id: UUID,
    student_id: UUID,
    target_lesson_id: UUID,
    operator_id: UUID,
) -> tuple[LessonLedger, Lesson, Lesson]:
    """Compensate one missed lesson: ``grant`` +1 and a seat on the target.

    Idempotency: a make-up ``grant`` row always carries the missed lesson in
    ``lesson_id`` (every other ``grant`` has ``lesson_id = NULL``), so a second
    make-up for the same absence is detected by a plain lookup. The +1 is
    attached to the student's soonest-to-expire active package, so the
    compensated lesson is the next one spent. The student is also added to the
    target lesson's attendance roster with status ``makeup``; the check-in
    state machine (step 3) will take that row over from there.
    """

    missed = await db.get(Lesson, lesson_id)
    if missed is None:
        raise AppError(404, "LESSON_NOT_FOUND", "缺勤课次不存在")
    target = await db.get(Lesson, target_lesson_id)
    if target is None:
        raise AppError(404, "LESSON_NOT_FOUND", "补课课次不存在")
    if target.id == missed.id:
        raise AppError(422, "MAKEUP_TARGET_INVALID", "补课课次不能是缺勤课次本身")

    student = await _get_student_or_404(db, student_id)
    if student.status != "active":
        raise AppError(422, "STUDENT_STATUS_INVALID", "学生不在读，不能补课")

    enrolled = await db.scalar(
        select(ClassEnrollment.id).where(
            ClassEnrollment.class_id == missed.class_id,
            ClassEnrollment.student_id == student_id,
        )
    )
    if enrolled is None:
        raise AppError(404, "ENROLLMENT_NOT_FOUND", "学生不在缺勤课次的班级")

    already = await db.scalar(
        select(LessonLedger.id).where(
            LessonLedger.student_id == student_id,
            LessonLedger.lesson_id == lesson_id,
            LessonLedger.type == "grant",
        )
    )
    if already is not None:
        raise AppError(409, "MAKEUP_ALREADY_GRANTED", "同一次缺勤只能补一次")

    today = business_today()
    purchase = await db.scalar(
        select(Purchase)
        .where(
            Purchase.student_id == student_id,
            or_(Purchase.expires_at.is_(None), Purchase.expires_at >= today),
        )
        .order_by(Purchase.expires_at.asc().nulls_last(), Purchase.purchased_at.asc())
    )
    if purchase is None:
        raise AppError(409, "MAKEUP_NO_ACTIVE_PACKAGE", "学生没有有效期内的课时包")

    entry = LessonLedger(
        tenant_id=tenant_id,
        student_id=student_id,
        purchase_id=purchase.id,
        lesson_id=missed.id,
        type="grant",
        delta=1,
        reason=(
            f"补课：缺勤 {missed.start_at:%Y-%m-%d %H:%M}，转入 {target.start_at:%Y-%m-%d %H:%M}"
        ),
        operator_id=operator_id,
    )
    db.add(entry)
    await db.flush()

    existing_seat = await db.scalar(
        select(Attendance.id).where(
            Attendance.lesson_id == target.id,
            Attendance.student_id == student_id,
        )
    )
    if existing_seat is None:
        try:
            async with db.begin_nested():
                db.add(
                    Attendance(
                        tenant_id=tenant_id,
                        lesson_id=target.id,
                        student_id=student_id,
                        status="makeup",
                        deduct_lessons=0,
                        operator_id=operator_id,
                    )
                )
        except IntegrityError:
            # Concurrent request seated the student first; the grant row above
            # is still the idempotency guard for the absence itself.
            pass

    return entry, missed, target
