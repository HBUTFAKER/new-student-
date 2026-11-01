"""Staff account administration schemas (US-301 create / manage / self-deactivate)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

StaffRole = Literal["admin", "registrar", "teacher"]
StaffStatus = Literal["active", "inactive"]


class StaffCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    phone: str = Field(min_length=1, max_length=32)
    role: StaffRole
    password: str = Field(min_length=8, max_length=72)
    teacher_id: UUID | None = None


class StaffUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    role: StaffRole | None = None
    teacher_id: UUID | None = None
    status: StaffStatus | None = None
    password: str | None = Field(default=None, min_length=8, max_length=72)


class StaffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    phone: str
    role: str
    status: str
    teacher_id: UUID | None
    created_at: datetime


class StaffCreateResponse(StaffOut):
    revived: bool = False


class StaffList(BaseModel):
    items: list[StaffOut]
    total: int


class SelfDeactivateRequest(BaseModel):
    password: str = Field(min_length=1, max_length=72)
