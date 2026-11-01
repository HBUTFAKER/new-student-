"""短信通道（6.3）：把 6.1 通知中心引擎接到一条「发文本到手机号」的抽象短信客户端。

与 6.2 ``wechat_subscribe`` 的区别同 ``wechat_mp``：本通道**不写站内信兜底**，无手机号 /
未配置供应商 / 退订 / 超预算 / 超时等永久性失败一律 ``raise ChannelSkipped`` 让降级链继续
下一级（0.7 链里短信已是自动链末端，之后是二期的人工跟进）；临时失败才抛普通异常重试。

日预算（清单验收原文）：发送前统计当日该租户短信成功条数，达到 ``org_config.sms_daily_budget``
即跳过本通道（不报错、不重试），日志记 ``sms_budget_exhausted``。成功条数 = 当日
``notification`` 里 ``'sms' = ANY(succeeded_channels)`` 的条数（一条通知 = 一条短信，6.4 按
家长拆分 enqueue 后天然对齐），COUNT 统计即可、不为预算加表。

裁决（发令部已定，照做不讨论）：

* **无手机号 / 未配置供应商 / 退订 → 直接跳过**。``parent.phone`` 为空视为无手机号；
  退订暂无判据数据源（无 opt-out 表），一期不实现、留给后续。
* **供应商 stub 默认**；real 切点 ``build_sms_client(settings)``，一期不绑定具体厂商，
  接口按「发一条文本到手机号」抽象。
* **TIMEOUT_UNKNOWN**：请求已发出但超时/无法确认上游是否接收 → 不重试，直接
  ``ChannelSkipped("TIMEOUT_UNKNOWN")``。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from datetime import time as dt_time
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.archive import Parent
from app.models.notification_center import Notification
from app.models.org import OrgConfig
from app.services.ledger import BUSINESS_TZ
from app.services.notification_center import (
    ChannelSkipped,
    NotificationContext,
    register_channel,
)

logger = logging.getLogger(__name__)

CHANNEL_SMS = "sms"

#: 机构级短信日预算缺省值（org_config 无行时回落到此）。6.4 起默认 200（发令部拍板）。
DEFAULT_SMS_DAILY_BUDGET = 200


class SmsError(Exception):
    """一次短信发送失败。``timeout=True`` 表示结果未知（TIMEOUT_UNKNOWN）；其余为临时。"""

    def __init__(self, message: str = "", *, timeout: bool = False) -> None:
        super().__init__(message or "sms send failed")
        self.timeout = timeout


class SmsClient(Protocol):
    """可替换的短信客户端：发一条文本到一个手机号。"""

    async def send(self, phone: str, content: str) -> None: ...


class StubSmsClient:
    """默认离线实现：记录每次调用，测试注入「下次抛异常 / 超时」钩子。

    ``fail_next_with`` 触发临时失败（通道抛普通异常让 6.1 重试）；``timeout_next`` 触发
    TIMEOUT_UNKNOWN（通道 ``ChannelSkipped`` 跳到下一级）。钩子一次性。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_next_with: Exception | None = None
        self.timeout_next: bool = False

    async def send(self, phone: str, content: str) -> None:
        self.calls.append((phone, content))
        if self.fail_next_with is not None:
            exc, self.fail_next_with = self.fail_next_with, None
            raise exc
        if self.timeout_next:
            self.timeout_next = False
            raise SmsError("request timed out", timeout=True)


class RealSmsClient:
    """生产占位：一期不绑定具体厂商，接口按「发一条文本到手机号」抽象。

    接真实厂商时在此实现（调供应商 HTTP API）。CI/本地全走 stub，本类只构造不联网。
    """

    def __init__(self, provider: str) -> None:
        self._provider = provider

    async def send(self, phone: str, content: str) -> None:
        raise NotImplementedError("SMS vendor not bound yet")


def build_sms_client(settings: Settings) -> SmsClient:
    """按环境选客户端：stub/local/test 走离线 stub，生产走真实现（占位）。"""

    if settings.sms_stub_mode or settings.app_env in {"local", "test"}:
        return StubSmsClient()
    if not settings.sms_provider:
        raise RuntimeError("SMS_PROVIDER is required when stub mode is disabled")
    return RealSmsClient(settings.sms_provider)


def _business_day_start() -> datetime:
    """业务时区（UTC+8）下「今天 00:00」作为当日预算窗口起点。"""

    today = datetime.now(BUSINESS_TZ).date()
    return datetime.combine(today, dt_time.min, tzinfo=BUSINESS_TZ)


async def count_today_sms(db: AsyncSession, tenant_id: UUID) -> int:
    """当日该租户短信成功条数 = 当日 ``'sms' = ANY(succeeded_channels)`` 的通知条数。"""

    start = _business_day_start()
    end = start + timedelta(days=1)
    return int(
        await db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.tenant_id == tenant_id,
                Notification.succeeded_channels.contains(["sms"]),
                Notification.updated_at >= start,
                Notification.updated_at < end,
            )
        )
        or 0
    )


async def daily_sms_budget(db: AsyncSession, tenant_id: UUID) -> int:
    """机构级短信日预算；org_config 无行时回落默认 200。"""

    value = await db.scalar(
        select(OrgConfig.sms_daily_budget).where(OrgConfig.tenant_id == tenant_id)
    )
    return int(value) if value is not None else DEFAULT_SMS_DAILY_BUDGET


class SmsChannel:
    """6.1 的通道适配器：短信。永久失败 = ``ChannelSkipped`` 继续下一级（或终态 exhausted）。

    ``client`` 缺省按环境构建；测试注入 ``StubSmsClient`` 覆盖。``settings`` 缺省读
    ``get_settings()``。
    """

    def __init__(
        self,
        client: SmsClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._client = client
        self._settings = settings

    def _settings_for(self) -> Settings:
        return self._settings or get_settings()

    def _client_for(self) -> SmsClient:
        return self._client or build_sms_client(self._settings_for())

    def _configured(self) -> bool:
        return bool(self._settings_for().sms_provider)

    async def send(self, ctx: NotificationContext) -> None:
        parent_ids = ctx.payload.get("parent_ids")
        if not parent_ids:
            raise ValueError("sms payload 缺 parent_ids")
        if not self._configured():
            raise ChannelSkipped("CHANNEL_NOT_CONFIGURED")
        if await self._budget_exhausted(ctx.db, ctx.tenant_id):
            logger.warning(
                "sms_budget_exhausted",
                extra={
                    "extra_fields": {
                        "dedupe_key": ctx.dedupe_key,
                        "tenant_id": str(ctx.tenant_id),
                    }
                },
            )
            raise ChannelSkipped("SMS_BUDGET_EXHAUSTED")

        client = self._client_for()
        sent_any = False
        for parent_id in parent_ids:
            phone = await self._resolve_phone(ctx.db, parent_id)
            if not phone:
                continue  # 无手机号 → 跳过该家长
            content = self._build_content(ctx)
            try:
                await client.send(phone, content)
            except SmsError as exc:
                if exc.timeout:
                    logger.warning(
                        "timeout_unknown",
                        extra={
                            "extra_fields": {
                                "channel": CHANNEL_SMS,
                                "dedupe_key": ctx.dedupe_key,
                            }
                        },
                    )
                    raise ChannelSkipped("TIMEOUT_UNKNOWN") from exc
                raise  # 临时失败：抛异常让 6.1 重试
            sent_any = True

        if not sent_any:
            raise ChannelSkipped("SMS_NO_VERIFIED_PHONE")

    async def _budget_exhausted(self, db: AsyncSession, tenant_id: UUID) -> bool:
        budget = await daily_sms_budget(db, tenant_id)
        used = await count_today_sms(db, tenant_id)
        return used >= budget

    async def _resolve_phone(self, db: AsyncSession, parent_id: str | UUID) -> str | None:
        phone = await db.scalar(select(Parent.phone).where(Parent.id == UUID(str(parent_id))))
        return phone or None

    def _build_content(self, ctx: NotificationContext) -> str:
        if ctx.payload.get("content"):
            return str(ctx.payload["content"])
        title = ctx.payload.get("title")
        body = ctx.payload.get("body")
        if title and body:
            return f"{title}：{body}"
        return str(title or body or "通知")


register_channel(CHANNEL_SMS, SmsChannel())
