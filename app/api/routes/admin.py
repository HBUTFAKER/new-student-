"""管理端通知测试发送接口（6.2，人工验证 + 部署后真机调试用）。

这是 6.2 唯一会 ``enqueue`` 的地方——真正的业务事件（上课提醒 / 余额预警）由 6.4 的
定时任务接入。它只走 6.1 引擎的幂等入队，不自己发微信、不碰额度扣减（那些都在
``wechat_subscribe`` 通道里）。
"""

from uuid import uuid4

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import TenantDbSession
from app.api.permissions import OfficeUser
from app.core.errors import AppError
from app.models.archive import Parent
from app.models.notification_center import Notification
from app.schemas.admin import NotificationTestRequest, NotificationTestResponse
from app.services.notification_center import enqueue_notification

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
