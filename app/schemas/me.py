"""Request/response schemas for parent self-service endpoints."""

from datetime import date, datetime
from typing import Literal
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


class SubscribeTemplateItem(BaseModel):
    """§10 占位 + 5.4：微信订阅消息模板（后端配置下发，小程序不硬编码）。"""

    template_id: str
    type: str
    scene: str


class SubscribeTemplateList(BaseModel):
    items: list[SubscribeTemplateItem]


class SubscribeAuthResult(BaseModel):
    """家长在 ``wx.requestSubscribeMessage`` 弹窗里的选择。

    没有 ``wechat_user_id`` 字段：身份只能来自 token（06 文档红线）。
    """

    template_id: str
    status: Literal["accept", "reject", "ban"]


class SubscribeAuthRequest(BaseModel):
    results: list[SubscribeAuthResult] = Field(min_length=1, max_length=20)


class SubscribeAuthItem(BaseModel):
    """一条授权记录的最新状态 + 剩余可发送额度（6.2 扣这个数）。"""

    template_id: str
    status: str
    available_count: int


class SubscribeAuthResponse(BaseModel):
    items: list[SubscribeAuthItem]
    total: int
