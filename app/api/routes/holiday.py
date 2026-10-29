"""Tenant holiday endpoints (3.2).

机构节假日只影响课次生成（跳过这些日期），所以读写都收口到 OfficeUser。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.deps import TenantDbSession
from app.api.permissions import OfficeUser
from app.models.holiday import TenantHoliday
from app.schemas.holiday import (
    TenantHolidayBatchOut,
    TenantHolidayCreate,
    TenantHolidayList,
    TenantHolidayOut,
)
from app.services.holiday import add_holidays, delete_holiday, list_holidays

router = APIRouter(tags=["holiday"])

Year = Annotated[int, Query(ge=2000, le=2100)]


def _holiday_view(holiday: TenantHoliday) -> TenantHolidayOut:
    return TenantHolidayOut(
        id=holiday.id,
        date=holiday.holiday_date,
        name=holiday.name,
        created_at=holiday.created_at,
    )


@router.post("/tenant-holidays", response_model=TenantHolidayBatchOut, summary="批量登记节假日")
async def create_tenant_holidays(
    payload: TenantHolidayCreate, db: TenantDbSession, principal: OfficeUser
) -> TenantHolidayBatchOut:
    holidays, created = await add_holidays(
        db, tenant_id=principal.tenant_id, dates=payload.dates, name=payload.name
    )
    return TenantHolidayBatchOut(
        items=[_holiday_view(holiday) for holiday in holidays],
        created=created,
        skipped=len(payload.dates) - created,
    )


@router.get("/tenant-holidays", response_model=TenantHolidayList, summary="节假日列表")
async def list_tenant_holidays(
    db: TenantDbSession, principal: OfficeUser, year: Year | None = None
) -> TenantHolidayList:
    holidays = await list_holidays(db, year=year)
    return TenantHolidayList(
        items=[_holiday_view(holiday) for holiday in holidays], total=len(holidays)
    )


@router.delete("/tenant-holidays/{holiday_id}", status_code=204, summary="删除节假日")
async def delete_tenant_holiday(
    holiday_id: UUID, db: TenantDbSession, principal: OfficeUser
) -> Response:
    await delete_holiday(db, holiday_id)
    return Response(status_code=204)
