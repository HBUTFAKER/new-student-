"""Report schemas (4.4). Contract: the three read-only endpoints of step 4.4.

一期扩容说明：``docs/phase0/05-api-spec.md`` §8 写的是「报表一期无接口」，
4.4 施工清单要求先做出勤率 / 课耗 / 老师课时三个只读报表，故一期扩这三个，
其余经营报表仍在二期。
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Window(BaseModel):
    """``from``/``to`` 回显；`from` 是 Python 关键字，用别名输出。"""

    model_config = ConfigDict(populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: datetime


class ClassAttendanceRow(BaseModel):
    class_id: UUID
    class_name: str
    lesson_count: int
    present: int
    leave: int
    absent: int
    # 分母为 0 时为 null（前端显示「无数据」，不是 0%）
    attendance_rate: int | None


class StudentAttendanceRow(BaseModel):
    student_id: UUID
    student_name: str
    present: int
    leave: int
    absent: int
    attendance_rate: int | None


class AttendanceSummaryOut(_Window):
    items: list[ClassAttendanceRow]
    total: int
    # 仅 detail=true 时按学生展开；未请求时为空数组（字段始终存在）
    student_rows: list[StudentAttendanceRow] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class StudentConsumptionRow(BaseModel):
    student_id: UUID
    student_name: str
    total_consumed: int
    lesson_count: int


class ClassConsumptionRow(BaseModel):
    student_id: UUID
    class_id: UUID | None
    class_name: str | None
    consumed: int
    lesson_count: int


class ConsumptionOut(_Window):
    items: list[StudentConsumptionRow]
    total: int
    by_class: list[ClassConsumptionRow]


class TeacherHoursRow(BaseModel):
    teacher_id: UUID
    teacher_name: str
    lesson_count: int
    completed_count: int
    total_minutes: int


class TeacherHoursOut(_Window):
    items: list[TeacherHoursRow]
    total: int
