"""Tenant holiday schemas (3.2)."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TenantHolidayCreate(BaseModel):
    """批量登记：一次提交多个日期，重复日期自动计入 skipped。"""

    dates: list[date] = Field(min_length=1, max_length=60)
    name: str | None = Field(default=None, max_length=64)


class TenantHolidayOut(BaseModel):
    id: UUID
    date: date
    name: str | None
    created_at: datetime


class TenantHolidayBatchOut(BaseModel):
    items: list[TenantHolidayOut]
    created: int
    skipped: int


class TenantHolidayList(BaseModel):
    items: list[TenantHolidayOut]
    total: int
