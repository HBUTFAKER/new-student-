"""Lesson schemas (3.2)."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class LessonGenerate(BaseModel):
    class_id: UUID
    weeks: int = Field(
        ge=1, le=52, description="从起始日（规律 start_date 与今天取大）起生成多少周"
    )


class LessonGenerationOut(BaseModel):
    class_id: UUID
    from_date: date
    to_date: date
    generated: int
    skipped_holidays: int
    skipped_existing: int


class LessonOut(BaseModel):
    id: UUID
    class_id: UUID
    class_name: str
    classroom_id: UUID
    classroom_name: str
    teacher_id: UUID
    teacher_name: str
    start_at: datetime
    end_at: datetime
    status: str


class LessonList(BaseModel):
    items: list[LessonOut]
    total: int
