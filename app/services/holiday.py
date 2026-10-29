"""Tenant holiday business logic (3.2).

Holidays are the only source of "no lesson today": nothing here knows about
national calendars, so a tenant that works through the Spring Festival simply
registers nothing.
"""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.holiday import TenantHoliday


async def add_holidays(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    dates: Sequence[date],
    name: str | None,
) -> tuple[list[TenantHoliday], int]:
    """Register dates in one call; already-known dates count as skipped.

    Returns every holiday that matches the requested dates (created + already
    present) so the caller can echo the resulting state, plus how many were new.
    """

    requested = list(dict.fromkeys(dates))
    existing = set(
        await db.scalars(
            select(TenantHoliday.holiday_date).where(TenantHoliday.holiday_date.in_(requested))
        )
    )
    fresh = [day for day in requested if day not in existing]
    if fresh:
        db.add_all(TenantHoliday(tenant_id=tenant_id, holiday_date=day, name=name) for day in fresh)
        try:
            await db.flush()
        except IntegrityError as exc:
            # 两个请求同时登记同一天：唯一键兜底，整批回滚，让调用方重取。
            raise AppError(409, "HOLIDAY_DUPLICATED", "节假日已存在，请刷新后重试") from exc

    rows = await db.scalars(
        select(TenantHoliday)
        .where(TenantHoliday.holiday_date.in_(requested))
        .order_by(TenantHoliday.holiday_date, TenantHoliday.id)
    )
    return list(rows), len(fresh)


async def list_holidays(db: AsyncSession, *, year: int | None = None) -> list[TenantHoliday]:
    stmt = select(TenantHoliday)
    if year is not None:
        # 用半开区间而不是 extract()，让 (tenant_id, date) 唯一索引可用。
        stmt = stmt.where(
            TenantHoliday.holiday_date >= date(year, 1, 1),
            TenantHoliday.holiday_date < date(year + 1, 1, 1),
        )
    rows = await db.scalars(stmt.order_by(TenantHoliday.holiday_date, TenantHoliday.id))
    return list(rows)


async def get_holiday(db: AsyncSession, holiday_id: UUID) -> TenantHoliday:
    holiday = await db.get(TenantHoliday, holiday_id)
    if holiday is None:
        raise AppError(404, "HOLIDAY_NOT_FOUND", "节假日不存在")
    return holiday


async def delete_holiday(db: AsyncSession, holiday_id: UUID) -> None:
    holiday = await get_holiday(db, holiday_id)
    await db.delete(holiday)
    await db.flush()


async def holiday_dates(db: AsyncSession, start: date, end: date) -> set[date]:
    """Closed dates inside ``[start, end]``; consumed by lesson generation."""

    rows = await db.scalars(
        select(TenantHoliday.holiday_date).where(
            TenantHoliday.holiday_date >= start,
            TenantHoliday.holiday_date <= end,
        )
    )
    return set(rows)
