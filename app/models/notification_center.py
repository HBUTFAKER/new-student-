"""通知中心（6.1）：一条待发/待重试的出站通知（引擎层，不是具体业务消息）。

与 ``in_app_message``（5.5）的分工：

* ``notification`` 是**引擎**：它只管「这条事件要发给哪些通道、发到哪一步、要不要重试」，
  ``payload`` 里装着通道各自解读的参数。
* ``in_app_message`` 是**产物**：``in_app`` 通道发送成功后会在这里落一行，供家长端读。
  两者幂等键同构，靠 ``(source_type, source_id, parent_id)`` 天然不冲突。

``dedupe_key`` 是事件幂等的根：同一业务事件重复入队只会留一行（``UNIQUE`` + 入队
``ON CONFLICT DO NOTHING``）。``updated_at`` 追踪重试时间，所以是本项目第 3 张带
``updated_at`` 的表（前两张 ``org_config`` / ``subscribe_auth``）。
"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, TenantMixin, UUIDPrimaryKeyMixin


class Notification(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """一条出站通知的投递状态机。

    状态机只有三个值：``pending → succeeded``（全部通道成功）或
    ``pending → exhausted``（重试次数用尽，终态）。失败不单独存一态：失败后仍留在
    ``pending``，只是 ``attempts + 1``、``next_retry_at`` 按指数退避后移，等 worker
    下次扫到再试。``succeeded_channels`` 记录已经成功的通道，重试只发剩余通道，保证
    部分成功时不重复发。
    """

    __tablename__ = "notification"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_notification_dedupe_key"),
        # 后台 worker 跨租户扫描「到点待发」队列用的索引。这是平台级队列索引，
        # 不是业务查询，所以不从 tenant_id 起（见 test_models 的例外登记）。
        Index("ix_notification_status_next_retry_at", "status", "next_retry_at"),
    )

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    channels: Mapped[list[str]] = mapped_column(
        ARRAY(String(16)), nullable=False, server_default=text("'{}'::text[]")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    next_retry_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    succeeded_channels: Mapped[list[str] | None] = mapped_column(ARRAY(String(16)), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=text("now()"), onupdate=func.now()
    )
