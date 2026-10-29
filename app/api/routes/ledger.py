"""Lesson packages, purchases, ledger and balance endpoints (2.3 / 2.4)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantDbSession
from app.api.permissions import OfficeUser, get_parent_scope, require_any
from app.core.auth import Principal
from app.core.errors import AppError
from app.models.archive import Student
from app.models.course import Class, ClassEnrollment
from app.models.ledger import LessonLedger
from app.models.schedule import Lesson
from app.schemas.ledger import (
    AdjustmentCreate,
    AdjustmentOut,
    BalanceOut,
    LedgerEntryOut,
    LedgerList,
    LedgerType,
    PackageList,
    PackageView,
    PurchaseCreate,
    PurchaseOut,
)
from app.services.ledger import (
    adjust_lessons,
    create_purchase,
    ledger_query,
    student_packages,
    total_balance,
)

router = APIRouter(tags=["ledger"])

Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]

# 0.2 矩阵：教务/管理员全量，老师与学生级收窄，家长只读绑定孩子。
LedgerAudience = Annotated[
    Principal,
    Depends(require_any("admin", "registrar", "teacher", "parent")),
]


async def _authorize_student(db: AsyncSession, principal: Principal, student_id: UUID) -> Student:
    """Third gate for the student-scoped reads (tenant RLS already applied)."""

    if principal.role == "parent":
        scope = await get_parent_scope(db, principal)
        binding = next(
            (item for item in scope.bindings if item.student_id == student_id),
            None,
        )
        if binding is None or binding.tenant_id != principal.tenant_id:
            raise AppError(403, "OUT_OF_SCOPE", "学生不在绑定集合内")
        student = await db.get(Student, student_id)
        if student is None:
            raise AppError(404, "STUDENT_NOT_FOUND", "学生不存在")
        return student

    student = await db.get(Student, student_id)
    if student is None:
        raise AppError(404, "STUDENT_NOT_FOUND", "学生不存在")
    if principal.role == "teacher":
        covered = await db.scalar(
            select(ClassEnrollment.id)
            .join(Class, Class.id == ClassEnrollment.class_id)
            .where(
                ClassEnrollment.student_id == student_id,
                Class.teacher_id == principal.teacher_id,
            )
            .limit(1)
        )
        if covered is None:
            raise AppError(403, "OUT_OF_SCOPE", "无权查看该学生")
    return student


@router.post("/purchases", response_model=PurchaseOut, status_code=201, summary="缴费登记")
async def register_purchase(
    payload: PurchaseCreate, db: TenantDbSession, principal: OfficeUser
) -> PurchaseOut:
    purchase, package, entries = await create_purchase(
        db,
        tenant_id=principal.tenant_id,
        student_id=payload.student_id,
        course_id=payload.course_id,
        lesson_count=payload.lesson_count,
        gift_lesson_count=payload.gift_lesson_count,
        valid_months=payload.valid_months,
        amount=payload.amount,
        operator_id=principal.subject_id,
    )
    balance = await total_balance(db, payload.student_id)
    return PurchaseOut(
        id=purchase.id,
        student_id=purchase.student_id,
        course_id=purchase.course_id,
        package_id=package.id,
        package_name=package.name,
        quantity=purchase.quantity,
        amount=purchase.amount,
        purchased_at=purchase.purchased_at,
        expires_at=purchase.expires_at,
        balance_after=balance,
        entries=[LedgerEntryOut.model_validate(entry) for entry in entries],
    )


@router.get(
    "/students/{student_id}/packages",
    response_model=PackageList,
    summary="学生课时包（余额实时求和）",
)
async def list_packages(
    student_id: UUID,
    db: TenantDbSession,
    principal: LedgerAudience,
    page: Page = 1,
    page_size: PageSize = 20,
) -> PackageList:
    await _authorize_student(db, principal, student_id)
    packages = await student_packages(db, student_id)
    window = packages[(page - 1) * page_size : page * page_size]
    return PackageList(
        items=[PackageView(**item) for item in window],
        total=len(packages),
        total_remaining=sum(item["remaining"] for item in packages),
    )


@router.get(
    "/students/{student_id}/ledger",
    response_model=LedgerList,
    summary="课时流水分页（只增，不可修改）",
)
async def list_ledger(
    student_id: UUID,
    db: TenantDbSession,
    principal: LedgerAudience,
    type: LedgerType | None = None,
    page: Page = 1,
    page_size: PageSize = 20,
) -> LedgerList:
    await _authorize_student(db, principal, student_id)

    stmt = ledger_query(student_id)
    if type is not None:
        stmt = stmt.where(LessonLedger.type == type)
    if principal.role == "teacher":
        # 0.2：老师只看自己任教班级产生的 consume 流水。
        taught = select(Class.id).where(Class.teacher_id == principal.teacher_id)
        stmt = stmt.where(
            or_(
                LessonLedger.type != "consume",
                LessonLedger.lesson_id.in_(select(Lesson.id).where(Lesson.class_id.in_(taught))),
            )
        )

    total = int(
        await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    )
    rows = await db.execute(
        stmt.order_by(LessonLedger.created_at.desc(), LessonLedger.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        LedgerEntryOut(
            id=entry.id,
            type=entry.type,
            delta=entry.delta,
            purchase_id=entry.purchase_id,
            lesson_id=entry.lesson_id,
            class_name=class_name,
            reason=entry.reason,
            operator_id=entry.operator_id,
            created_at=entry.created_at,
        )
        for entry, class_name in rows
    ]
    return LedgerList(items=items, total=total)


@router.get(
    "/students/{student_id}/balance",
    response_model=BalanceOut,
    summary="实时余额（SUM(delta)，无余额列）",
)
async def get_balance(
    student_id: UUID,
    db: TenantDbSession,
    principal: LedgerAudience,
) -> BalanceOut:
    await _authorize_student(db, principal, student_id)
    packages = await student_packages(db, student_id)
    return BalanceOut(
        student_id=student_id,
        total=await total_balance(db, student_id),
        packages=[PackageView(**item) for item in packages],
    )


@router.post(
    "/ledger/adjustments",
    response_model=AdjustmentOut,
    status_code=201,
    summary="课时调整（必填原因，独立流水留痕）",
)
async def create_adjustment(
    payload: AdjustmentCreate, db: TenantDbSession, principal: OfficeUser
) -> AdjustmentOut:
    entry = await adjust_lessons(
        db,
        tenant_id=principal.tenant_id,
        student_id=payload.student_id,
        purchase_id=payload.purchase_id,
        delta=payload.delta,
        reason=payload.reason,
        operator_id=principal.subject_id,
    )
    return AdjustmentOut.model_validate(entry)
