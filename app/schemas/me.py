"""Request/response schemas for parent self-service endpoints."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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


class MeScheduleItem(BaseModel):
    """§9.3 单节课。``status`` 除 ``lesson.status`` 外还可能是 ``makeup``（补课席位）。"""

    lesson_id: UUID
    class_name: str
    subject: str | None
    teacher_name: str | None
    start_at: datetime
    end_at: datetime
    status: str


class MeScheduleResponse(BaseModel):
    """§9.3。``?week=`` 按 032 裁决不做，改回显 ``from``/``to``。"""

    model_config = ConfigDict(populate_by_name=True)

    student_id: UUID
    from_: datetime = Field(alias="from")
    to: datetime
    items: list[MeScheduleItem]


class MeBalancePackage(BaseModel):
    """§9.4 课时包：只列仍有效的包（过期/耗尽的不下发）。"""

    purchase_id: UUID
    remaining: int
    expires_at: date | None


class MeBalanceResponse(BaseModel):
    student_id: UUID
    # 负数合法：欠课时照实返回，不裁剪成 0。
    total: int
    packages: list[MeBalancePackage]


class MeLedgerItem(BaseModel):
    """§9.5 流水行。家长不需要算子/备注/流水 ID，只给这四列。"""

    type: str
    delta: int
    class_name: str | None
    created_at: datetime


class MeLedgerResponse(BaseModel):
    items: list[MeLedgerItem]
    total: int
