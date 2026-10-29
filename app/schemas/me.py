"""Request/response schemas for parent self-service endpoints."""

from uuid import UUID

from pydantic import BaseModel


class ActiveStudentRequest(BaseModel):
    student_id: UUID


class ActiveStudentResponse(BaseModel):
    active_student_id: UUID
    tenant_id: UUID
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class MeStudentItem(BaseModel):
    student_id: UUID
    student_name: str
    tenant_id: UUID
    tenant_name: str
    relation: str | None
    active: bool


class MeStudentList(BaseModel):
    items: list[MeStudentItem]


class MeBindingRequest(BaseModel):
    invite_code: str


class MeBindingResponse(BaseModel):
    student_id: UUID
    student_name: str
    relation: str
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    tenant_id: UUID
    active_student_id: UUID
