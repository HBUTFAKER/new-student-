"""Trade module: purchase records and (phase 2) refunds."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, TenantMixin, UUIDPrimaryKeyMixin


class Purchase(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """购课记录（线下收款后登记，US-205）。

    登记时同事务写入一条 ``purchase`` 流水（见 0.5 API 清单）。
    """

    __tablename__ = "purchase"
    __table_args__ = (
        Index("ix_purchase_tenant_id_student_id", "tenant_id", "student_id"),
        Index("ix_purchase_tenant_id_package_id", "tenant_id", "package_id"),
        # 扣减选包顺序 + 加锁路径。
        Index(
            "ix_purchase_tenant_id_student_id_expires_at",
            "tenant_id",
            "student_id",
            "expires_at",
        ),
    )

    student_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("student.id"), nullable=False
    )
    # 一次缴费买的是哪门课；课时包模板本身不分课程（0.4 字典）。
    course_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("course.id"), nullable=False
    )
    package_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("course_package.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    purchased_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # purchased_at + course_package.valid_months，登记时计算写入。
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)


class Refund(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """退费记录（二期，US-211 / US-303 审批）。"""

    __tablename__ = "refund"
    __table_args__ = (
        Index("ix_refund_tenant_id_student_id", "tenant_id", "student_id"),
        Index("ix_refund_tenant_id_status", "tenant_id", "status"),
    )

    student_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("student.id"), nullable=False
    )
    purchase_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchase.id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    approver_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("staff_account.id"), nullable=True
    )
