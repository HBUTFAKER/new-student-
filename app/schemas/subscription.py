"""Subscription status & renewal schemas (7.2)."""

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field


class SubscriptionStatusResponse(BaseModel):
    plan_name: str
    end_date: date
    days_left: int
    expired: bool


class RenewRequest(BaseModel):
    days: int = Field(ge=1, description="顺延天数")
    plan_id: UUID | None = Field(default=None, description="可选，换套餐")


class RenewResponse(BaseModel):
    end_date: date
    plan_id: UUID
    plan_name: str
