"""Attendance module: one row per student per lesson (red line 3).

The unique constraint ``(tenant_id, lesson_id, student_id)`` *is* the idempotency
key of check-in: a repeated request hits the constraint and the service layer
returns the existing row instead of deducting lessons twice.
"""

from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, TenantMixin, UUIDPrimaryKeyMixin


class Attendance(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """签到记录（US-102 / US-208）。"""

    __tablename__ = "attendance"
    __table_args__ = (
        # 幂等键（红线三）：同一次课、同一个学生只允许一条签到记录。
        UniqueConstraint("tenant_id", "lesson_id", "student_id"),
        Index("ix_attendance_tenant_id_student_id", "tenant_id", "student_id"),
    )

    lesson_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("lesson.id"), nullable=False
    )
    student_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("student.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # 本次扣减课时：leave/absent 是否扣课按 org_config.absence_deduct_rule（US-217）。
    deduct_lessons: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    operator_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
