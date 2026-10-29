"""Course and class schemas (2.1 / 2.2)."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CourseStatus = Literal["active", "inactive"]
ClassStatus = Literal["active", "ended"]


class CourseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    subject: str | None = Field(default=None, max_length=64)
    duration_min: int | None = Field(default=None, ge=5, le=600)
    remark: str | None = Field(default=None, max_length=255)


class CourseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    subject: str | None = Field(default=None, max_length=64)
    duration_min: int | None = Field(default=None, ge=5, le=600)
    remark: str | None = Field(default=None, max_length=255)
    status: CourseStatus | None = None


class CourseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    subject: str | None
    duration_min: int | None
    remark: str | None
    status: str
    created_at: datetime


class CourseList(BaseModel):
    items: list[CourseOut]
    total: int


class ClassCreate(BaseModel):
    course_id: UUID
    teacher_id: UUID
    classroom_id: UUID
    name: str = Field(min_length=1, max_length=128)
    term_start: date
    term_end: date
    capacity: int | None = Field(default=None, gt=0, le=10_000)


class ClassUpdate(BaseModel):
    teacher_id: UUID | None = None
    classroom_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    term_start: date | None = None
    term_end: date | None = None
    capacity: int | None = Field(default=None, gt=0, le=10_000)
    status: ClassStatus | None = None


class ClassOut(BaseModel):
    id: UUID
    name: str
    course_id: UUID
    course_name: str
    teacher_id: UUID
    teacher_name: str
    classroom_id: UUID
    classroom_name: str
    classroom_capacity: int
    capacity: int | None
    enrolled: int
    term_start: date | None
    term_end: date | None
    status: str
    created_at: datetime


class ClassList(BaseModel):
    items: list[ClassOut]
    total: int


class EnrollmentCreate(BaseModel):
    student_id: UUID


class EnrollmentOut(BaseModel):
    id: UUID
    student_id: UUID
    student_name: str
    student_status: str
    joined_at: date


class EnrollmentList(BaseModel):
    items: list[EnrollmentOut]
    total: int
