"""Lesson feedback (048): teacher writes a post-class note for parents to read.

一期收官步：老师课后给家长写「今天讲了啥、孩子表现如何」。一期纯文字，
不建独立编号 / 评分 / 图片字段（打分、图片二期）。

``teacher_id`` 是反馈归属的任课老师（恒等于 ``lesson.teacher_id``）：老师本人写时
即操作者本人，教务 / admin 代写时也归到任课老师名下 —— 一期字段定死，不单独记
代写人。家长端「老师名」从这里 join，保证显示的是这节课的任课老师。

``updated_at`` 是第 4 张带该列的表（前三张 org_config / subscribe_auth / notification），
记录最后一次覆盖更新的时间。
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, TenantMixin, UUIDPrimaryKeyMixin


class LessonFeedback(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """一条课后反馈。唯一约束 ``(lesson_id, student_id)`` = 一个学生一节课最多一条。"""

    __tablename__ = "lesson_feedback"
    __table_args__ = (
        UniqueConstraint("lesson_id", "student_id", name="uq_lesson_feedback_lesson_student"),
        Index("ix_lesson_feedback_tenant_id_lesson_id", "tenant_id", "lesson_id"),
        # 家长端按孩子倒序翻页：tenant_id 起（红线），student_id 等值，created_at DESC。
        Index(
            "ix_lesson_feedback_tenant_id_student_id_created_at",
            "tenant_id",
            "student_id",
            text("created_at DESC"),
        ),
    )

    lesson_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("lesson.id"), nullable=False
    )
    student_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("student.id"), nullable=False
    )
    teacher_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("teacher.id"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=text("now()"), onupdate=func.now()
    )
