"""Lesson ledger: the append-only source of truth for lesson balances.

Red line 2: the balance is always ``SUM(delta)`` over this table. There is no
balance column and no ``updated_at``; rows are inserted and never updated.

``purchase_id`` / ``lesson_id`` are physically nullable because non-consume
entries (purchase, refund, adjust, transfer) have no package or lesson to point
at. The rule "consume 必填" is enforced in the service layer, not by a CHECK
constraint, so that future entry types do not require a migration.
"""

from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, TenantMixin, UUIDPrimaryKeyMixin


class LessonLedger(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """课时流水（只增）。"""

    __tablename__ = "lesson_ledger"
    __table_args__ = (
        Index(
            "ix_lesson_ledger_tenant_id_student_id_created_at",
            "tenant_id",
            "student_id",
            "created_at",
        ),
        Index("ix_lesson_ledger_tenant_id_type", "tenant_id", "type"),
        Index("ix_lesson_ledger_tenant_id_lesson_id", "tenant_id", "lesson_id"),
        # 包余额 = 按 purchase_id 分组 SUM(delta)，见 2.4。
        Index("ix_lesson_ledger_tenant_id_purchase_id", "tenant_id", "purchase_id"),
    )

    student_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("student.id"), nullable=False
    )
    # consume 类型必填（应用层保证），其余类型可空。
    purchase_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchase.id"), nullable=True
    )
    # consume 类型必填（应用层保证）；支撑家长明细与签到互查。
    lesson_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("lesson.id"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    # 正=增，负=减；插入后不可修改。
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operator_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
