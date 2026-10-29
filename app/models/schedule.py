"""Scheduling module: recurring rules and concrete lessons."""

from datetime import date, datetime, time
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Time, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, TenantMixin, UUIDPrimaryKeyMixin


class ScheduleRule(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """排课规律（US-207）：weekday 0-6 + 时间区间 + 生效日期区间。"""

    __tablename__ = "schedule_rule"
    __table_args__ = (
        Index("ix_schedule_rule_tenant_id_class_id", "tenant_id", "class_id"),
        Index(
            "ix_schedule_rule_tenant_id_teacher_id_weekday", "tenant_id", "teacher_id", "weekday"
        ),
        Index(
            "ix_schedule_rule_tenant_id_classroom_id_weekday",
            "tenant_id",
            "classroom_id",
            "weekday",
        ),
    )

    class_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("class.id"), nullable=False
    )
    classroom_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("classroom.id"), nullable=False
    )
    teacher_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("teacher.id"), nullable=False
    )
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class Lesson(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """课次（US-207 / US-212）。

    时间重叠的数据库级防护在阶段 3 用 PG 排他约束
    （``tstzrange`` + ``EXCLUDE USING gist``）实现，这里只有普通索引。
    """

    __tablename__ = "lesson"
    __table_args__ = (
        Index("ix_lesson_tenant_id_class_id_start_at", "tenant_id", "class_id", "start_at"),
        Index("ix_lesson_tenant_id_teacher_id_start_at", "tenant_id", "teacher_id", "start_at"),
        Index("ix_lesson_tenant_id_classroom_id_start_at", "tenant_id", "classroom_id", "start_at"),
    )

    class_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("class.id"), nullable=False
    )
    classroom_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("classroom.id"), nullable=False
    )
    teacher_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("teacher.id"), nullable=False
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'normal'"))
