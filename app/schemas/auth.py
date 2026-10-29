"""Request/response schemas for authentication endpoints."""

from uuid import UUID

from pydantic import BaseModel, Field


class StaffLoginRequest(BaseModel):
    phone: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class WechatLoginRequest(BaseModel):
    wx_code: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class StaffTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str
    tenant_id: UUID


class WechatTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str = "parent"
    tenant_id: UUID | None
    active_student_id: UUID | None
    bound_students: int


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
