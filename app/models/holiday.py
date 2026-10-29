"""Tenant holidays: dates on which no lesson is generated (3.2).

The 0.4 dictionary has no holiday concept, so step 3.2 adds exactly one minimal
table. Storage stays dumb on purpose — one row per closed date, no recurrence
rules, no national calendar. Lesson generation reads it and skips those dates,
which keeps every "which day is closed" decision inside the tenant's own data.
"""

from datetime import date

from sqlalchemy import Date, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, TenantMixin, UUIDPrimaryKeyMixin


class TenantHoliday(UUIDPrimaryKeyMixin, CreatedAtMixin, TenantMixin, Base):
    """机构节假日（一天一行，同租户同一天只能登记一次）。"""

    __tablename__ = "tenant_holiday"
    __table_args__ = (UniqueConstraint("tenant_id", "date"),)

    # 属性名避开 datetime.date，数据库列名仍是字典要求的 date。
    holiday_date: Mapped[date] = mapped_column("date", Date, nullable=False)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
