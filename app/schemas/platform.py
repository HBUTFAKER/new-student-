"""Platform operations schemas (internal provisioning only in phase 1)."""

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field


class TenantCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    admin_name: str = Field(min_length=1, max_length=64)
    admin_phone: str = Field(min_length=1, max_length=32)
    admin_password: str = Field(min_length=8, max_length=128)


class TenantCreateResponse(BaseModel):
    tenant_id: UUID
    name: str
    admin_staff_id: UUID
    admin_phone: str
    plan_id: UUID


class RegisterRequest(BaseModel):
    """Public self-serve registration (7.1); field constraints mirror TenantCreateRequest."""

    org_name: str = Field(min_length=1, max_length=128)
    admin_name: str = Field(min_length=1, max_length=64)
    admin_phone: str = Field(min_length=1, max_length=32)
    admin_password: str = Field(min_length=8, max_length=128)


class RegisterResponse(BaseModel):
    """Registration result — no token; the admin signs in through the normal login page."""

    tenant_id: UUID
    name: str
    admin_phone: str
    plan_id: UUID
    trial_end_date: date


class TenantUsageResponse(BaseModel):
    """租户用量（7.3，7.4 平台后台租户详情页数据源）。"""

    plan_name: str
    max_students: int
    students_used: int
    max_messages: int
    messages_used: int
    period: str
    subscription_end_date: date
