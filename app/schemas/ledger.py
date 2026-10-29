"""Lesson package, purchase and ledger schemas (2.3 / 2.4)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

LedgerType = Literal["purchase", "grant", "consume", "adjust", "refund", "expire"]
PackageStatus = Literal["active", "expired", "exhausted"]


class PurchaseCreate(BaseModel):
    """缴费登记：课时包定义随缴费内联，登记时固化成快照。"""

    student_id: UUID
    course_id: UUID
    lesson_count: int = Field(ge=1, le=10_000)
    gift_lesson_count: int = Field(default=0, ge=0, le=10_000)
    valid_months: int = Field(ge=1, le=120)
    amount: Decimal = Field(ge=0, max_digits=10, decimal_places=2)


class LedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    delta: int
    purchase_id: UUID | None
    lesson_id: UUID | None
    class_name: str | None = None
    reason: str | None
    operator_id: UUID
    created_at: datetime


class LedgerList(BaseModel):
    items: list[LedgerEntryOut]
    total: int


class PurchaseOut(BaseModel):
    id: UUID
    student_id: UUID
    course_id: UUID
    package_id: UUID
    package_name: str
    quantity: int
    amount: Decimal
    purchased_at: datetime
    expires_at: date
    balance_after: int
    entries: list[LedgerEntryOut]


class PackageView(BaseModel):
    purchase_id: UUID
    course_id: UUID
    course_name: str
    package_name: str
    granted: int
    remaining: int
    purchased_at: datetime
    expires_at: date | None
    status: PackageStatus


class PackageList(BaseModel):
    items: list[PackageView]
    total: int
    total_remaining: int


class BalanceOut(BaseModel):
    student_id: UUID
    total: int
    packages: list[PackageView]


class AdjustmentCreate(BaseModel):
    """课时调整（US-209）；reason 为空在路由层拒绝为 422 REASON_REQUIRED。"""

    student_id: UUID
    purchase_id: UUID
    delta: int = Field(ge=-10_000, le=10_000)
    reason: str | None = Field(default=None, max_length=255)


class AdjustmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    delta: int
    purchase_id: UUID | None
    reason: str | None
    operator_id: UUID
    created_at: datetime
