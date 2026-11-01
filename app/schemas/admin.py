"""管理端通知测试接口的请求/响应 schema（6.2）。"""

from uuid import UUID

from pydantic import BaseModel, Field


class NotificationTestRequest(BaseModel):
    """人工验证 / 真机调试用的测试发送入参。"""

    parent_id: UUID
    template_scene: str = Field(min_length=1, max_length=32)
    channels: list[str] = Field(default_factory=lambda: ["wechat_subscribe"])


class NotificationTestResponse(BaseModel):
    notification_id: UUID
