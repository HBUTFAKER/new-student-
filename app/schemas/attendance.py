"""Attendance schemas (3.5): roll call, result query, absence deduction rule.

Contract follows ``docs/phase0/05-api-spec.md`` §6.1–6.3.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

AttendanceStatus = Literal["present", "leave", "absent"]


class AttendanceRecordIn(BaseModel):
    student_id: UUID
    status: AttendanceStatus


class AttendanceMarkIn(BaseModel):
    records: list[AttendanceRecordIn] = Field(min_length=1)


class AttendanceResultOut(BaseModel):
    student_id: UUID
    # present / leave / absent / failed
    status: str
    deduct_lessons: int = 0
    deduplicated: bool = False
    # 仅 failed 出现，目前只有 BALANCE_EXHAUSTED
    reason: str | None = None


class AttendanceMarkOut(BaseModel):
    lesson_id: UUID
    results: list[AttendanceResultOut]


class AttendanceRowOut(BaseModel):
    student_id: UUID
    status: str
    deduct_lessons: int
    operator_id: UUID


class AttendanceList(BaseModel):
    items: list[AttendanceRowOut]
    total: int


class AbsenceRuleOut(BaseModel):
    absence_deduct_rule: str
    updated_by: UUID | None = None
    updated_at: datetime | None = None


class AbsenceRuleUpdate(BaseModel):
    # 取值校验放 service，好回 422 RULE_VALUE_INVALID 而不是通用的 VALIDATION_ERROR。
    absence_deduct_rule: str = Field(min_length=1, max_length=16)
