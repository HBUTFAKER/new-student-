"""Archive module: students, parents, teachers, classrooms and bindings."""

from uuid import UUID

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, TenantMixin, UUIDPrimaryKeyMixin


class Student(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """学生档案。删除 = 改 status（active/suspended/archived），不物理删除。"""

    __tablename__ = "student"
    __table_args__ = (
        Index("ix_student_tenant_id_status", "tenant_id", "status"),
        Index("ix_student_tenant_id_name", "tenant_id", "name"),
    )

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    grade: Mapped[str | None] = mapped_column(String(16), nullable=True)
    school: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))
    contact: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Parent(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """家长档案（租户内），通过 wechat_user_id 关联平台级微信身份。"""

    __tablename__ = "parent"
    __table_args__ = (Index("ix_parent_tenant_id_phone", "tenant_id", "phone"),)

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    wechat_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("wechat_user.id"), nullable=True
    )


class Teacher(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """老师档案。离职 = status 停用，不删档。"""

    __tablename__ = "teacher"
    __table_args__ = (Index("ix_teacher_tenant_id_status", "tenant_id", "status"),)

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))


class Classroom(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """教室档案（US-216），容量与可用时段供排课冲突校验使用。"""

    __tablename__ = "classroom"
    __table_args__ = (Index("ix_classroom_tenant_id", "tenant_id"),)

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    # [{"weekday": 1, "start": "18:00", "end": "21:00"}, ...]
    available_slots: Mapped[list | None] = mapped_column(JSONB, nullable=True)


class ParentStudent(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """家长-学生多对多绑定（US-202），同一对孩子只能绑定一次。"""

    __tablename__ = "parent_student"
    __table_args__ = (
        UniqueConstraint("tenant_id", "parent_id", "student_id"),
        Index("ix_parent_student_tenant_id_student_id", "tenant_id", "student_id"),
    )

    parent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("parent.id"), nullable=False
    )
    student_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("student.id"), nullable=False
    )
    relation: Mapped[str | None] = mapped_column(String(16), nullable=True)
