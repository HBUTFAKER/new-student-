"""Lesson schemas (3.2 / 3.3 / 3.4)."""

from datetime import date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


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


def _require_aware(value: datetime, name: str) -> datetime:
    """3.4：时间入参必须带时区，naive 直接 422。"""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} 必须带时区（aware datetime）")
    return value


class LessonCreate(BaseModel):
    """单次加课（3.4，US-207），复用 service 的 create_lesson 走冲突预检。"""

    class_id: UUID
    classroom_id: UUID
    teacher_id: UUID
    start_at: datetime
    end_at: datetime

    @field_validator("start_at")
    @classmethod
    def _start_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "start_at")

    @field_validator("end_at")
    @classmethod
    def _end_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "end_at")

    @model_validator(mode="after")
    def _range(self) -> Self:
        if self.start_at >= self.end_at:
            raise ValueError("end_at 必须晚于 start_at")
        return self


class LessonUpdate(BaseModel):
    """调课/取消（3.4）：时间/教室/老师任选其一以上；status='cancelled' 表示取消。"""

    start_at: datetime | None = None
    end_at: datetime | None = None
    classroom_id: UUID | None = None
    teacher_id: UUID | None = None
    status: Literal["cancelled"] | None = None

    @field_validator("start_at", "end_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        return _require_aware(value, "时间")

    @model_validator(mode="after")
    def _range(self) -> Self:
        if self.start_at is not None and self.end_at is not None and self.start_at >= self.end_at:
            raise ValueError("end_at 必须晚于 start_at")
        return self


class MakeupLessonCreate(BaseModel):
    """补课排期（3.4，US-212）：把缺勤学员排进另一节未来课次。"""

    student_id: UUID
    absent_lesson_id: UUID
    target_lesson_id: UUID
    reason: str | None = None


class MakeupLessonOut(BaseModel):
    id: UUID
    student_id: UUID
    target_lesson_id: UUID
    status: str
