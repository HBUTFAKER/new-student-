"""Request/response schemas for lesson feedback (048)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class FeedbackItemIn(BaseModel):
    """批量写反馈里的单条：一个学生一段文字。"""

    student_id: UUID
    content: str = Field(min_length=1, max_length=2000)


class FeedbackUpsertRequest(BaseModel):
    """PUT /lessons/{lesson_id}/feedback 的请求体，一次可写多个学生。"""

    items: list[FeedbackItemIn] = Field(min_length=1, max_length=100)


class FeedbackRow(BaseModel):
    """管理端某课次一条学生反馈。没写的学生不出现在列表里（048 口径）。"""

    student_id: UUID
    student_name: str
    teacher_id: UUID
    content: str
    updated_at: datetime | None


class FeedbackList(BaseModel):
    lesson_id: UUID
    items: list[FeedbackRow]
    total: int


class ChildFeedbackItem(BaseModel):
    """家长端一条反馈：课次日期 + 课程/班级名 + 老师名 + 内容。"""

    lesson_id: UUID
    lesson_date: datetime
    class_name: str
    course_name: str | None
    teacher_name: str
    content: str
    created_at: datetime


class ChildFeedbackList(BaseModel):
    items: list[ChildFeedbackItem]
    total: int
