"""Organisation module: staff accounts and per-tenant configuration."""

from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, TenantMixin, UUIDPrimaryKeyMixin


class StaffAccount(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """员工账号（US-301）：admin / registrar / teacher，手机号租户内唯一。"""

    __tablename__ = "staff_account"
    __table_args__ = (
        UniqueConstraint("tenant_id", "phone"),
        Index("ix_staff_account_tenant_id_role", "tenant_id", "role"),
    )

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    # argon2id hash. Empty server default keeps the 24-table migration additive;
    # login rejects empty hashes, and account creation must always set one.
    password_hash: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=text("''")
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    # 老师角色关联任教身份（teacher.id）；教务/管理员为空。
    teacher_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("teacher.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))


class OrgConfig(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """机构配置，一机构一条（US-217 / US-305）。"""

    __tablename__ = "org_config"
    __table_args__ = (UniqueConstraint("tenant_id"),)

    # none=不扣 / deduct=照扣；请假与缺勤通用（US-217）。
    absence_deduct_rule: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'none'")
    )
    logo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notify_template: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
