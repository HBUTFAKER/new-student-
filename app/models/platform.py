"""Platform-layer models (they belong to the SaaS operator, not to a tenant).

Only ``tenant_subscription`` and ``usage_meter`` carry ``tenant_id``; ``tenant``
itself and ``plan`` are platform-wide rows. These tables are intentionally *not*
tenant-scoped by RLS: the platform administrator must be able to aggregate
across tenants (0.3 decision 6).
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Index, Integer, Numeric, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin


class Tenant(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Training / hosting organisation (a tenant)."""

    __tablename__ = "tenant"
    __table_args__ = (Index("ix_tenant_name", "name"),)

    # The dictionary marks a unique constraint on name as optional ("视运营需要");
    # it is intentionally not created so two organisations may share a display name.
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))


class Plan(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """SaaS plan (phase 2, US-402)."""

    __tablename__ = "plan"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    max_students: Mapped[int] = mapped_column(Integer, nullable=False)
    max_messages: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)


class TenantSubscription(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Tenant subscription to a plan (phase 2)."""

    __tablename__ = "tenant_subscription"
    __table_args__ = (Index("ix_tenant_subscription_tenant_id", "tenant_id"),)

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenant.id"), nullable=False
    )
    plan_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("plan.id"), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))


class UsageMeter(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Per-period usage counters (phase 2, US-403)."""

    __tablename__ = "usage_meter"
    __table_args__ = (
        Index(
            "uq_usage_meter_tenant_id_metric_period",
            "tenant_id",
            "metric",
            "period",
            unique=True,
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenant.id"), nullable=False
    )
    metric: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    period: Mapped[str] = mapped_column(String(16), nullable=False)
