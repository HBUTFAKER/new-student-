"""WeChat identity layer.

``wechat_user`` and ``wechat_openid`` are platform-level (no ``tenant_id``):
one WeChat person may have children in several tenants and must be recognised
as the same human across the mini program and future service accounts
(0.6 decision 2). ``subscribe_auth`` is tenant-scoped because message quotas are
consumed per tenant (phase 2).
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin


class WechatUser(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A WeChat person, merged across appids by unionid."""

    __tablename__ = "wechat_user"

    unionid: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    nickname: Mapped[str | None] = mapped_column(String(64), nullable=True)


class WechatOpenid(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """openid → wechat_user mapping; one row per appid."""

    __tablename__ = "wechat_openid"
    __table_args__ = (
        UniqueConstraint("app_id", "openid"),
        Index("ix_wechat_openid_wechat_user_id", "wechat_user_id"),
    )

    wechat_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("wechat_user.id"), nullable=False
    )
    app_id: Mapped[str] = mapped_column(String(64), nullable=False)
    openid: Mapped[str] = mapped_column(String(64), nullable=False)


class SubscribeAuth(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Subscription-message authorisation record (5.4 collects, 6.2 sends)."""

    __tablename__ = "subscribe_auth"
    __table_args__ = (UniqueConstraint("tenant_id", "wechat_user_id", "template_id"),)

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenant.id"), nullable=False
    )
    wechat_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("wechat_user.id"), nullable=False
    )
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'authorized'")
    )
    # 一次性订阅的语义：家长每「接受」一次就多一次可发送额度，重复接受继续累加。
    # 6.2 发一条扣 1，扣到 0 就不能再发（reject/ban 不清零，已经攒下的额度仍有效）。
    available_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
