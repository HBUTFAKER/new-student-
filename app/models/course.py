"""Course / class module: courses, classes, enrolment and lesson packages."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, TenantMixin, UUIDPrimaryKeyMixin


class Course(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """课程定义。"""

    __tablename__ = "course"
    __table_args__ = (Index("ix_course_tenant_id", "tenant_id"),)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Class(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """班级（US-204）。表名 ``class``，故类名为 Class（不是 Python 关键字）。"""

    __tablename__ = "class"
    __table_args__ = (
        Index("ix_class_tenant_id_course_id", "tenant_id", "course_id"),
        Index("ix_class_tenant_id_teacher_id", "tenant_id", "teacher_id"),
        Index("ix_class_tenant_id_classroom_id", "tenant_id", "classroom_id"),
    )

    course_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("course.id"), nullable=False
    )
    teacher_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("teacher.id"), nullable=False
    )
    classroom_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("classroom.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ClassEnrollment(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """班级学员名单（US-204），同班同学只能入读一次。"""

    __tablename__ = "class_enrollment"
    __table_args__ = (
        UniqueConstraint("tenant_id", "class_id", "student_id"),
        Index("ix_class_enrollment_tenant_id_student_id", "tenant_id", "student_id"),
    )

    class_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("class.id"), nullable=False
    )
    student_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("student.id"), nullable=False
    )
    joined_at: Mapped[date] = mapped_column(Date, nullable=False)


class CoursePackage(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """课时包定义（US-205），valid_months 为购后有效期。"""

    __tablename__ = "course_package"
    __table_args__ = (Index("ix_course_package_tenant_id", "tenant_id"),)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    total_lessons: Mapped[int] = mapped_column(Integer, nullable=False)
    # Counter used in balance arithmetic, so NULL is not allowed even though the
    # dictionary only spells out "默认 0".
    gift_lessons: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    valid_months: Mapped[int] = mapped_column(Integer, nullable=False)
