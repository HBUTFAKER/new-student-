"""Lesson schemas (3.2 / 3.3)."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class LessonGenerate(BaseModel):
    class_id: UUID
    weeks: int = Field(
        ge=1, le=52, description="从起始日（规律 start_date 与今天取大）起生成多少周"
    )


class ConflictOut(BaseModel):
    """3.3 冲突明细，type 按 老师→教室→学员 聚合，时间 +08:00。"""

    type: Literal["teacher", "classroom", "student"]
    lesson_id: UUID
    class_id: UUID
    start_at: datetime
    end_at: datetime


class LessonGenerationOut(BaseModel):
    class_id: UUID
    from_date: date
    to_date: date
    generated: int
    skipped_holidays: int
    skipped_existing: int
    skipped_conflicts: int = 0
    conflicts: list[ConflictOut] = Field(default_factory=list)


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
