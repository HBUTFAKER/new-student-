"""Platform operations schemas (internal provisioning only in phase 1)."""

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
