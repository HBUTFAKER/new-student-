"""管理端通知测试接口的请求/响应 schema（6.2）。"""

from uuid import UUID

from pydantic import BaseModel, Field


class NotificationTestRequest(BaseModel):
    """人工验证 / 真机调试用的测试发送入参。"""

    parent_id: UUID
    template_scene: str = Field(min_length=1, max_length=32)
    # 6.4 起默认走完整降级链：订阅 → 服务号 → 短信 → 站内信（见 0.7 时序图）。
    channels: list[str] = Field(
        default_factory=lambda: ["wechat_subscribe", "wechat_mp", "sms", "in_app"]
    )


class NotificationTestResponse(BaseModel):
    notification_id: UUID


class SmsUsageResponse(BaseModel):
    """机构当日短信用量：已发条数 / 预算 / 剩余。"""

    used: int
    budget: int
    remaining: int


class NotificationStats(BaseModel):
    """通知中心一个时间窗口内的统计（6.4）。"""

    total: int
    succeeded: int
    exhausted: int
    pending: int
    # 成功率 = succeeded / total；分母为 0 时返回 null（不是 100%）。
    success_rate: float | None


class NotificationsStatsResponse(BaseModel):
    """通知中心统计：当日 + 近 7 天（6.4）。"""

    today: NotificationStats
    last_7_days: NotificationStats


class NotificationRetryResponse(BaseModel):
    """漏发补发（retry）的结果（6.4）。"""

    notification_id: UUID
    status: str
