"""管理端通知接口（6.2 测试发送 + 6.3 短信用量 + 6.4 统计/补发）。

这是 6.2 唯一会 ``enqueue`` 的地方——真正的业务事件（上课提醒 / 余额预警）由 6.4 的
定时任务接入。它只走 6.1 引擎的幂等入队，不自己发微信、不碰额度扣减（那些都在
``wechat_subscribe`` 通道里）。
"""

from datetime import datetime, timedelta
from datetime import time as dt_time
from uuid import UUID, uuid4

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import TenantDbSession
from app.api.permissions import OfficeUser
from app.core.errors import AppError
from app.models.archive import Parent
from app.models.notification_center import Notification
from app.schemas.admin import (
    NotificationRetryResponse,
    NotificationsStatsResponse,
    NotificationStats,
    NotificationTestRequest,
    NotificationTestResponse,
    SmsUsageResponse,
)
from app.services.channels.sms import count_today_sms, daily_sms_budget
from app.services.ledger import BUSINESS_TZ
from app.services.notification_center import (
    STATUS_EXHAUSTED,
    STATUS_PENDING,
    STATUS_SUCCEEDED,
    enqueue_notification,
)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post(
    "/notifications/test",
    response_model=NotificationTestResponse,
    summary="测试发送一条通知（微信订阅 + 站内信兜底）",
)
async def test_notification(
    payload: NotificationTestRequest,
    db: TenantDbSession,
    principal: OfficeUser,
) -> NotificationTestResponse:
    """给指定家长发一条测试通知，家长端效果直接看微信 / 站内信。

    * ``dedupe_key`` 用 ``test:{uuid4()}`` —— 测试事件**故意不幂等**，每次点都真发一次
      （这是 6.2 唯一的幂等例外，见 08 §0 交接块）；
    * 家长不在当前机构 → 404（跨租户读 404 是 0.3 口径，RLS 先挡、这里再「查得到才算」）。
    """

    tenant_id = principal.tenant_id
    if tenant_id is None:
        raise AppError(403, "TENANT_CONTEXT_REQUIRED", "请先选择孩子")

    parent = (
        await db.execute(
            select(Parent.id).where(
                Parent.id == payload.parent_id,
                Parent.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if parent is None:
        raise AppError(404, "PARENT_NOT_FOUND", "家长不存在")

    dedupe_key = f"test:{uuid4()}"
    await enqueue_notification(
        db,
        tenant_id=tenant_id,
        event_type="notification_test",
        dedupe_key=dedupe_key,
        payload={
            "parent_ids": [str(payload.parent_id)],
            "template_scene": payload.template_scene,
            "title": "测试通知",
            "body": f"这是一条测试通知（scene={payload.template_scene}）",
            "source_type": "notification_test",
            "source_id": str(uuid4()),
        },
        channels=payload.channels,
    )

    notification_id = await db.scalar(
        select(Notification.id).where(Notification.dedupe_key == dedupe_key)
    )
    return NotificationTestResponse(notification_id=notification_id)


@router.get(
    "/sms-usage",
    response_model=SmsUsageResponse,
    summary="查询机构当日短信用量（6.3）",
)
async def sms_usage(
    db: TenantDbSession,
    principal: OfficeUser,
    tenant_id: UUID | None = None,
) -> SmsUsageResponse:
    """查当日该租户短信已发条数 + 预算 + 剩余。

    * ``tenant_id`` 省略 = 查当前登录机构；显式传且与当前机构不一致 → 404（跨租户读 404，
      0.3 口径）；
    * 收口 admin/registrar，老师 / 家长 403 ``OUT_OF_SCOPE``。
    """

    own_tenant = principal.tenant_id
    if own_tenant is None:
        raise AppError(403, "TENANT_CONTEXT_REQUIRED", "请先选择孩子")
    if tenant_id is not None and tenant_id != own_tenant:
        raise AppError(404, "TENANT_NOT_FOUND", "机构不存在")

    used = await count_today_sms(db, own_tenant)
    budget = await daily_sms_budget(db, own_tenant)
    return SmsUsageResponse(used=used, budget=budget, remaining=max(budget - used, 0))


def _business_day_start(day: datetime | None = None) -> datetime:
    """业务时区（UTC+8）下某天的 00:00，作为统计窗口起点。"""

    base = (day or datetime.now(BUSINESS_TZ)).astimezone(BUSINESS_TZ).date()
    return datetime.combine(base, dt_time.min, tzinfo=BUSINESS_TZ)


async def _stats_window(
    db: TenantDbSession, tenant_id: UUID, start: datetime, end: datetime
) -> NotificationStats:
    rows = (
        await db.execute(
            select(Notification.status, func.count())
            .where(
                Notification.tenant_id == tenant_id,
                Notification.created_at >= start,
                Notification.created_at < end,
            )
            .group_by(Notification.status)
        )
    ).all()
    counts = {status: int(n) for status, n in rows}
    total = sum(counts.values())
    succeeded = counts.get(STATUS_SUCCEEDED, 0)
    exhausted = counts.get(STATUS_EXHAUSTED, 0)
    pending = counts.get(STATUS_PENDING, 0)
    success_rate = round(succeeded / total, 4) if total else None
    return NotificationStats(
        total=total,
        succeeded=succeeded,
        exhausted=exhausted,
        pending=pending,
        success_rate=success_rate,
    )


@router.get(
    "/notifications/stats",
    response_model=NotificationsStatsResponse,
    summary="通知中心统计：近 7 天 + 当日成功率（6.4）",
)
async def notifications_stats(
    db: TenantDbSession,
    principal: OfficeUser,
) -> NotificationsStatsResponse:
    """返回当日与近 7 天（含今日）的通知量 / 状态分布 / 成功率。

    * 成功率 = succeeded / total（入队口径），分母 0 时返回 null（不是 100%）；
    * 收口 admin/registrar，老师 / 家长 403 ``OUT_OF_SCOPE``；本接口无 tenant_id 参数，
      只查当前登录机构（跨租户天然不可见，RLS 兜底）。
    """

    own_tenant = principal.tenant_id
    if own_tenant is None:
        raise AppError(403, "TENANT_CONTEXT_REQUIRED", "请先选择孩子")

    tomorrow = _business_day_start() + timedelta(days=1)
    today_start = _business_day_start()
    seven_start = today_start - timedelta(days=6)

    today = await _stats_window(db, own_tenant, today_start, tomorrow)
    last_7_days = await _stats_window(db, own_tenant, seven_start, tomorrow)
    return NotificationsStatsResponse(today=today, last_7_days=last_7_days)


@router.post(
    "/notifications/{notification_id}/retry",
    response_model=NotificationRetryResponse,
    summary="补发一条已耗尽的漏发通知（6.4）",
)
async def retry_notification(
    notification_id: UUID,
    db: TenantDbSession,
    principal: OfficeUser,
) -> NotificationRetryResponse:
    """把 ``exhausted`` 通知重置回 pending（attempts=0、next_retry_at=now）补发。

    * ``succeeded`` 返回 409 禁止重发；``pending``（仍在队）也 409；
    * 跨租户 id → 404（RLS 先挡，这里「查得到才算你的」再判一道）；
    * 收口 admin/registrar，老师 / 家长 403。
    """

    own_tenant = principal.tenant_id
    if own_tenant is None:
        raise AppError(403, "TENANT_CONTEXT_REQUIRED", "请先选择孩子")

    notification = await db.get(Notification, notification_id)
    if notification is None or notification.tenant_id != own_tenant:
        raise AppError(404, "NOTIFICATION_NOT_FOUND", "通知不存在")

    if notification.status == STATUS_SUCCEEDED:
        raise AppError(409, "NOTIFICATION_ALREADY_SUCCEEDED", "已送达的通知不能重发")
    if notification.status != STATUS_EXHAUSTED:
        raise AppError(409, "NOTIFICATION_NOT_RETRYABLE", "仅已耗尽的通知可以补发")

    notification.status = STATUS_PENDING
    notification.attempts = 0
    notification.next_retry_at = datetime.now(BUSINESS_TZ)
    notification.last_error = None
    notification.succeeded_channels = []
    await db.flush()
    return NotificationRetryResponse(notification_id=notification.id, status=notification.status)
