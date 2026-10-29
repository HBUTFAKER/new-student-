"""Schedule rule schemas (3.1).

weekday 沿用 ``date.weekday()`` 口径：0=周一 … 6=周日。3.2 生成课次时按同一
约定换算，前端不要再做偏移。

表结构（04-data-dictionary）一行只存一个 weekday，所以「每周二四」= 两行，
``weekdays`` 只是写入时的批量便捷字段；「共 8 周」也不落库，由 start_date 与
end_date 表达（``weeks`` 仅用于推导 end_date）。
"""

from datetime import date, datetime, time, timedelta
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.lesson import ConflictOut

Weekday = Annotated[int, Field(ge=0, le=6, description="0=周一 … 6=周日")]
WeekdayList = Annotated[list[Weekday], Field(min_length=1, max_length=7)]


class ScheduleRuleCreate(BaseModel):
    """新建规律：weekdays（或单个 weekday）取其一，end_date 与 weeks 取其一。"""

    class_id: UUID
    classroom_id: UUID
    teacher_id: UUID
    weekday: Weekday | None = None
    weekdays: WeekdayList | None = None
    start_time: time
    end_time: time
    start_date: date
    end_date: date | None = None
    weeks: int | None = Field(
        default=None, ge=1, le=104, description="共 N 周（含首尾），与 end_date 二选一"
    )

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        if (self.weekday is None) == (self.weekdays is None):
            raise ValueError("weekday 与 weekdays 必须且只能提供一个")
        if self.weekdays is not None and len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError("weekdays 不能重复")
        if self.end_date is not None and self.weeks is not None:
            raise ValueError("end_date 与 weeks 必须且只能提供一个")
        if self.end_date is None and self.weeks is None:
            raise ValueError("end_date 与 weeks 必须提供一个")
        return self

    def resolved_weekdays(self) -> list[int]:
        days = set(self.weekdays or ())
        if self.weekday is not None:
            days.add(self.weekday)
        return sorted(days)

    def resolved_end_date(self) -> date:
        if self.end_date is not None:
            return self.end_date
        # 含首尾的 N 周：第 N 周最后一天 = start_date + 7N - 1 天。
        return self.start_date + timedelta(days=7 * (self.weeks or 1) - 1)


class ScheduleRuleUpdate(BaseModel):
    """局部更新；class_id 不可改（换班请删旧建新）。"""

    classroom_id: UUID | None = None
    teacher_id: UUID | None = None
    weekday: Weekday | None = None
    start_time: time | None = None
    end_time: time | None = None
    start_date: date | None = None
    end_date: date | None = None


class ScheduleRuleOut(BaseModel):
    id: UUID
    class_id: UUID
    class_name: str
    classroom_id: UUID
    classroom_name: str
    teacher_id: UUID
    teacher_name: str
    weekday: int
    start_time: time
    end_time: time
    start_date: date
    end_date: date | None
    created_at: datetime


class ScheduleRuleList(BaseModel):
    items: list[ScheduleRuleOut]
    total: int


class ScheduleRuleValidateOut(BaseModel):
    """3.3 冲突预检：不落库，仅返回是否冲突与明细。"""

    ok: bool
    conflicts: list[ConflictOut] = Field(default_factory=list)
