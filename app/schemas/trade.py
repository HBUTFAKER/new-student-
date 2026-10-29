"""Refund / transfer / make-up schemas (step 2.5)."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RefundCreate(BaseModel):
    """退费登记：退的是指定课时包的课时数，剩余部分不做隐式清零。"""

    student_id: UUID
    purchase_id: UUID
    refund_lessons: int = Field(ge=1, le=10_000)
    refund_amount: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    reason: str | None = Field(default=None, max_length=255)


class RefundOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    delta: int
    purchase_id: UUID | None
    lesson_id: UUID | None
    reason: str | None
    operator_id: UUID
    created_at: datetime


class TransferCreate(BaseModel):
    student_id: UUID
    from_class_id: UUID
    to_class_id: UUID


class TransferOut(BaseModel):
    student_id: UUID
    from_class_id: UUID
    to_class_id: UUID
    joined_at: date


class MakeUpCreate(BaseModel):
    student_id: UUID
    target_lesson_id: UUID


class MakeUpOut(BaseModel):
    id: UUID
    type: str
    delta: int
    purchase_id: UUID | None
    lesson_id: UUID | None
    reason: str | None
    target_lesson_id: UUID
    operator_id: UUID
    created_at: datetime
