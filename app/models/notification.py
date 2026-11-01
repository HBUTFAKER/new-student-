"""In-app messages (5.5): the fallback channel when WeChat subscription is off."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, TenantMixin, UUIDPrimaryKeyMixin


class InAppMessage(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """一条发给某个家长的站内信。

    幂等键 ``(source_type, source_id, parent_id)``：同一个业务事件对同一个家长只会
    留一条 —— 6.1 的通知中心直接复用这个键，别另起一套状态机。

    ``read_at`` 是读侧标记（NULL = 未读），不是 ``updated_at``，所以
    ``test_no_table_has_updated_at`` 不管它。消息按 ``parent_id`` 存而不是
    ``wechat_user_id``：家长换手机重新登录，历史消息还能看到。
    """

    __tablename__ = "in_app_message"
    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_id",
            "parent_id",
            name="uq_in_app_message_source",
        ),
        Index(
            "ix_in_app_message_tenant_id_parent_id_created_at",
            "tenant_id",
            "parent_id",
            text("created_at DESC"),
        ),
    )

    parent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("parent.id"), nullable=False
    )
    student_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("student.id"), nullable=True
    )
    # 本步只有 lesson_deduct；VARCHAR 留枚举空间，6.1 加类型不用改表。
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
