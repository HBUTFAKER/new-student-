"""Refund / transfer / make-up endpoints (step 2.5)."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter

from app.api.deps import TenantDbSession
from app.api.permissions import OfficeUser
from app.schemas.trade import (
    MakeUpCreate,
    MakeUpOut,
    RefundCreate,
    RefundOut,
    TransferCreate,
    TransferOut,
)
from app.services.trade import create_makeup, refund_lessons, transfer_student

router = APIRouter(tags=["trade"])


@router.post("/refunds", status_code=201, response_model=RefundOut, summary="退费（教务/管理员）")
async def create_refund(
    payload: RefundCreate, db: TenantDbSession, principal: OfficeUser
) -> RefundOut:
    entry = await refund_lessons(
        db,
        tenant_id=principal.tenant_id,
        student_id=payload.student_id,
        purchase_id=payload.purchase_id,
        refund_lessons=payload.refund_lessons,
        refund_amount=payload.refund_amount,
        reason=payload.reason,
        operator_id=principal.subject_id,
    )
    return RefundOut.model_validate(entry)


@router.post(
    "/transfers", status_code=201, response_model=TransferOut, summary="转班（教务/管理员）"
)
async def create_transfer(
    payload: TransferCreate, db: TenantDbSession, principal: OfficeUser
) -> TransferOut:
    _, to_class = await transfer_student(
        db,
        tenant_id=principal.tenant_id,
        student_id=payload.student_id,
        from_class_id=payload.from_class_id,
        to_class_id=payload.to_class_id,
        operator_id=principal.subject_id,
    )
    return TransferOut(
        student_id=payload.student_id,
        from_class_id=payload.from_class_id,
        to_class_id=payload.to_class_id,
        joined_at=date.today(),
    )


@router.post(
    "/lessons/{lesson_id}/make-ups",
    status_code=201,
    response_model=MakeUpOut,
    summary="补课登记（教务/管理员）",
)
async def create_makeup_endpoint(
    lesson_id: UUID, payload: MakeUpCreate, db: TenantDbSession, principal: OfficeUser
) -> MakeUpOut:
    entry, _, target = await create_makeup(
        db,
        tenant_id=principal.tenant_id,
        lesson_id=lesson_id,
        student_id=payload.student_id,
        target_lesson_id=payload.target_lesson_id,
        operator_id=principal.subject_id,
    )
    return MakeUpOut(
        id=entry.id,
        type=entry.type,
        delta=entry.delta,
        purchase_id=entry.purchase_id,
        lesson_id=entry.lesson_id,
        reason=entry.reason,
        target_lesson_id=target.id,
        operator_id=entry.operator_id,
        created_at=entry.created_at,
    )
