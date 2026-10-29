"""Archive module schemas: students, parents, teachers, classrooms, bindings, invites."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

StudentStatus = Literal["active", "suspended", "archived"]
TeacherStatus = Literal["active", "inactive"]


class StudentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    grade: str | None = Field(default=None, max_length=16)
    school: str | None = Field(default=None, max_length=128)
    contact: str | None = Field(default=None, max_length=64)
    status: StudentStatus = "active"


class StudentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    grade: str | None = Field(default=None, max_length=16)
    school: str | None = Field(default=None, max_length=128)
    contact: str | None = Field(default=None, max_length=64)
    status: StudentStatus | None = None


class StudentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    grade: str | None
    school: str | None
    contact: str | None
    status: str
    created_at: datetime


class StudentList(BaseModel):
    items: list[StudentOut]
    total: int


class ParentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    phone: str = Field(min_length=1, max_length=32)
    wechat_user_id: UUID | None = None


class ParentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    phone: str | None = Field(default=None, min_length=1, max_length=32)


class ParentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    phone: str
    wechat_user_id: UUID | None
    created_at: datetime


class ParentList(BaseModel):
    items: list[ParentOut]
    total: int


class TeacherCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    subject: str | None = Field(default=None, max_length=64)


class TeacherUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    subject: str | None = Field(default=None, max_length=64)
    status: TeacherStatus | None = None


class TeacherOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    phone: str | None
    subject: str | None
    status: str
    created_at: datetime


class TeacherList(BaseModel):
    items: list[TeacherOut]
    total: int


class ClassroomCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    capacity: int = Field(gt=0, le=10_000)
    available_slots: list[dict[str, Any]] | None = None


class ClassroomUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    capacity: int | None = Field(default=None, gt=0, le=10_000)
    available_slots: list[dict[str, Any]] | None = None


class ClassroomOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    capacity: int
    available_slots: list[dict[str, Any]] | None
    created_at: datetime


class ClassroomList(BaseModel):
    items: list[ClassroomOut]
    total: int


class BindingCreate(BaseModel):
    parent_id: UUID
    student_id: UUID
    relation: str | None = Field(default=None, max_length=16)


class BindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    parent_id: UUID
    student_id: UUID
    relation: str | None
    created_at: datetime


class BindingList(BaseModel):
    items: list[BindingOut]
    total: int


class InviteCodeCreate(BaseModel):
    student_id: UUID
    expires_in_hours: int = 24


class InviteCodeOut(BaseModel):
    code: str
    student_id: UUID
    status: str
    expires_at: datetime
    used_by: UUID | None = None


class InviteCodeList(BaseModel):
    items: list[InviteCodeOut]
