"""微信订阅消息通道（6.2）：把 6.1 通知中心引擎接到 ``subscribeMessage.send``。

本步只交付**通道 + 额度扣减 + 降级 + 一个管理端测试发送接口**，不接任何业务事件
（上课提醒 / 余额预警的 enqueue 是 6.4 定时任务的活）。

裁决（发令部已定，照做不讨论）：

* **先原子扣，扣到才发**：``UPDATE ... WHERE available_count > 0`` 抢到额度才调微信；
  发送失败 → 原子退还（+1）再抛异常（通知中心重试会重新扣）；抢不到 → 降级。
* **降级不是失败**：额度耗尽 / 模板 scene 不存在 / 找不到 openid / 未授权 / 微信永久
  拒绝（43101 拒收、40003 openid 无效、40037 模板无效、41030 页面非法）→ **不抛异常**，
  直接复用 ``in_app`` 通道的发送逻辑写一条站内信，通知标记该通道成功，不进重试状态机。
* **one_time 每次扣 1**；**long_term 不扣额度**，但要有 ``status='authorized'`` 的授权
  记录才能发。判断依据是配置里模板的 ``type``，不再查第二遍。
* openid 只经 ``parent.wechat_user_id → wechat_openid`` 解析，**不落日志、不进 payload**
  （06 红线）。

``payload`` 约定（6.4 接事件时按此填）：

* ``parent_ids``: ``[uuid, ...]`` —— 必填，收信家长；
* ``template_scene``: 必填，从 ``config.wechat_subscribe_templates`` 反查 ``template_id``；
* ``title`` / ``body``: 文案（也用于降级站内信的正文）；
* ``data``: 可选，微信模板字段的精确映射（生产真模板必须给，stub 可省）；
* ``source_type`` / ``source_id``: 降级站内信的幂等键（``source_id`` 必须是 UUID，与
  ``in_app_message`` 同构）；
* ``student_id``: 可选，消息涉及的孩子。

已知边界（记入 08 §0 交接块）：多家长事件里，若某家长**临时失败**，通道整体抛异常让
6.1 重试，重试会**重发已经成功的家长**（``succeeded_channels`` 只到通道粒度、不到家长
粒度）。6.4 事件接入时应按家长拆分 enqueue，或在 payload 里带 per-parent 已发标记，本步
不做。管理端测试接口是单家长，天然不受影响。
"""

from __future__ import annotations

import logging
import time
from typing import Protocol
from uuid import UUID

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, SubscribeTemplate, get_settings
from app.models.archive import Parent
from app.models.wechat import SubscribeAuth, WechatOpenid
from app.services.notification_center import (
    InAppChannel,
    NotificationContext,
    register_channel,
)

logger = logging.getLogger(__name__)

CHANNEL_WECHAT_SUBSCRIBE = "wechat_subscribe"

#: 微信侧**永久**错误码：命中即「降级」（写站内信、不重试）。这些都是「再试也白试」的
#: 错，重试只会浪费额度与重试次数。43101 用户拒收、40003 openid 无效、40037 模板 id
#: 无效、41030 页面路径非法。
PERMANENT_ERRCODES: frozenset[int] = frozenset({43101, 40003, 40037, 41030})

SUBSCRIBE_MESSAGE_URL = "https://api.weixin.qq.com/cgi-bin/message/subscribe/send"
ACCESS_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"


class WechatSubscribeError(Exception):
    """一次微信发送失败。``errcode`` 为 ``None`` 表示网络/HTTP 类临时失败；命中
    ``PERMANENT_ERRCODES`` 表示永久失败。通道据此决定「降级」还是「抛异常重试」。
    """

    def __init__(self, errcode: int | None, message: str = "") -> None:
        super().__init__(message or f"wechat subscribe send failed (errcode={errcode})")
        self.errcode = errcode


class WechatSubscribeClient(Protocol):
    """可替换的 ``subscribeMessage.send`` 客户端（跟 030 的 ``WechatAuthClient`` 同法）。"""

    async def send(self, openid: str, template_id: str, data: dict) -> None: ...


class StubWechatSubscribeClient:
    """默认离线实现：记录每次调用，测试注入「下次抛异常 / 返回永久错误码」钩子。

    ``fail_next_with`` 触发临时失败（通道会退还额度并抛异常让 6.1 重试）；
    ``reject_next_with`` 触发永久失败（通道会退还额度并降级写站内信）。钩子一次性。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.fail_next_with: Exception | None = None
        self.reject_next_with: int | None = None

    async def send(self, openid: str, template_id: str, data: dict) -> None:
        self.calls.append((openid, template_id, data))
        if self.fail_next_with is not None:
            exc, self.fail_next_with = self.fail_next_with, None
            raise exc
        if self.reject_next_with is not None:
            errcode, self.reject_next_with = self.reject_next_with, None
            raise WechatSubscribeError(errcode)


class RealWechatSubscribeClient:
    """生产实现：access_token 带缓存（提前 60s 过期），调 ``subscribeMessage.send``。

    默认**不启用**——``build_wechat_subscribe_client`` 在 stub mode / local / test 下都
    回落到 stub；只有关闭 ``WECHAT_STUB_MODE`` 且给了 ``WECHAT_APP_SECRET`` 才构造本类。
    """

    def __init__(self, app_id: str, app_secret: str) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def _access_token(self) -> str:
        now = time.monotonic()
        if self._token and self._token_expires_at > now:
            return self._token
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    ACCESS_TOKEN_URL,
                    params={
                        "grant_type": "client_credential",
                        "appid": self._app_id,
                        "secret": self._app_secret,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WechatSubscribeError(None, "get access_token failed") from exc
        token = payload.get("access_token")
        if not token:
            raise WechatSubscribeError(payload.get("errcode"), "get access_token rejected")
        self._token = token
        self._token_expires_at = now + int(payload.get("expires_in", 7200)) - 60
        return token

    async def send(self, openid: str, template_id: str, data: dict) -> None:
        try:
            token = await self._access_token()
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    SUBSCRIBE_MESSAGE_URL,
                    params={"access_token": token},
                    json={
                        "touser": openid,
                        "template_id": template_id,
                        "page": "pages/index/index",
                        "data": data,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WechatSubscribeError(None, "subscribe send request failed") from exc
        errcode = payload.get("errcode")
        if errcode != 0:
            raise WechatSubscribeError(errcode)


def build_wechat_subscribe_client(settings: Settings) -> WechatSubscribeClient:
    """按环境选客户端：stub/local/test 走离线 stub，生产走真实现。"""

    if settings.wechat_stub_mode or settings.app_env in {"local", "test"}:
        return StubWechatSubscribeClient()
    if not settings.wechat_app_secret:
        raise RuntimeError("WECHAT_APP_SECRET is required when stub mode is disabled")
    return RealWechatSubscribeClient(settings.wechat_app_id, settings.wechat_app_secret)


class WechatSubscribeChannel:
    """6.1 的通道适配器：one_time 原子扣额度后发，long_term 有授权即发，失败退还/降级。

    ``client`` 缺省按环境构建；测试注入 ``StubWechatSubscribeClient`` 覆盖默认。``settings``
    缺省读 ``get_settings()``（模板 scene → template_id 映射、小程序 app_id 都从它拿）。
    """

    def __init__(
        self,
        client: WechatSubscribeClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._client = client
        self._settings = settings

    def _client_for(self) -> WechatSubscribeClient:
        return self._client or build_wechat_subscribe_client(self._settings or get_settings())

    def _app_id(self) -> str:
        return (self._settings or get_settings()).wechat_app_id

    def _templates(self) -> dict[str, SubscribeTemplate]:
        return {t.scene: t for t in (self._settings or get_settings()).wechat_subscribe_templates}

    async def send(self, ctx: NotificationContext) -> None:
        parent_ids = ctx.payload.get("parent_ids")
        if not parent_ids:
            raise ValueError("wechat_subscribe payload 缺 parent_ids")
        scene = str(ctx.payload.get("template_scene") or "")
        if not scene:
            raise ValueError("wechat_subscribe payload 缺 template_scene")
        if not ctx.payload.get("source_id"):
            raise ValueError("wechat_subscribe payload 缺 source_id（降级站内信幂等键）")

        template = self._templates().get(scene)
        client = self._client_for()
        for parent_id in parent_ids:
            if template is None:
                await self._degrade(ctx, parent_id, f"模板 scene 不存在: {scene}")
                continue
            await self._send_to_parent(ctx, parent_id, template, client)

    async def _send_to_parent(
        self,
        ctx: NotificationContext,
        parent_id: str | UUID,
        template: SubscribeTemplate,
        client: WechatSubscribeClient,
    ) -> None:
        wechat_user_id = await self._resolve_wechat_user_id(ctx.db, parent_id)
        if wechat_user_id is None:
            await self._degrade(ctx, parent_id, "家长未绑定微信身份")
            return
        openid = await self._resolve_openid(ctx.db, wechat_user_id, self._app_id())
        if openid is None:
            await self._degrade(ctx, parent_id, "找不到小程序 openid")
            return

        auth = await self._load_auth(ctx.db, ctx.tenant_id, wechat_user_id, template.template_id)
        if template.type == "long_term":
            # 长期订阅：不扣额度，但要有 authorized 授权记录。
            if auth is None or auth.status != "authorized":
                await self._degrade(ctx, parent_id, "长期订阅未授权")
                return
        else:
            # 一次性订阅：只认额度，不查 status（5.4 定了 reject/ban 不清额度，额度没用完
            # 就仍可发，见 08 §0 交接块）。从没接受过 = 没记录 = 降级；抢不到额度 = 耗尽。
            if auth is None:
                await self._degrade(ctx, parent_id, "未授权该模板")
                return
            if not await self._decrement_quota(
                ctx.db, ctx.tenant_id, wechat_user_id, template.template_id
            ):
                await self._degrade(ctx, parent_id, "订阅额度耗尽")
                return

        data = self._build_data(ctx)
        try:
            await client.send(openid, template.template_id, data)
        except Exception as exc:  # noqa: BLE001 — 转成「退还+降级」或「退还+重试」
            if template.type == "one_time":
                await self._refund_quota(
                    ctx.db, ctx.tenant_id, wechat_user_id, template.template_id
                )
            if isinstance(exc, WechatSubscribeError) and exc.errcode in PERMANENT_ERRCODES:
                await self._degrade(ctx, parent_id, f"微信永久拒绝（errcode={exc.errcode}）")
                return
            raise  # 临时失败：抛异常让 6.1 重试（重试会重新扣）

    async def _resolve_wechat_user_id(self, db: AsyncSession, parent_id: str | UUID) -> UUID | None:
        return await db.scalar(
            select(Parent.wechat_user_id).where(Parent.id == UUID(str(parent_id)))
        )

    async def _resolve_openid(
        self, db: AsyncSession, wechat_user_id: UUID, app_id: str
    ) -> str | None:
        return await db.scalar(
            select(WechatOpenid.openid)
            .where(WechatOpenid.wechat_user_id == wechat_user_id, WechatOpenid.app_id == app_id)
            .limit(1)
        )

    async def _load_auth(
        self, db: AsyncSession, tenant_id: UUID, wechat_user_id: UUID, template_id: str
    ) -> SubscribeAuth | None:
        return (
            await db.execute(
                select(SubscribeAuth).where(
                    SubscribeAuth.tenant_id == tenant_id,
                    SubscribeAuth.wechat_user_id == wechat_user_id,
                    SubscribeAuth.template_id == template_id,
                )
            )
        ).scalar_one_or_none()

    async def _decrement_quota(
        self, db: AsyncSession, tenant_id: UUID, wechat_user_id: UUID, template_id: str
    ) -> bool:
        """原子扣 1：``WHERE available_count > 0`` 抢额度，影响 0 行 = 额度耗尽。"""

        result = await db.execute(
            update(SubscribeAuth)
            .where(
                SubscribeAuth.tenant_id == tenant_id,
                SubscribeAuth.wechat_user_id == wechat_user_id,
                SubscribeAuth.template_id == template_id,
                SubscribeAuth.available_count > 0,
            )
            .values(
                available_count=SubscribeAuth.available_count - 1,
                updated_at=func.now(),
            )
        )
        return bool(result.rowcount)

    async def _refund_quota(
        self, db: AsyncSession, tenant_id: UUID, wechat_user_id: UUID, template_id: str
    ) -> None:
        """原子退还 +1：发送失败后把抢到的额度还回去，避免重试时双重扣别人的额度。"""

        await db.execute(
            update(SubscribeAuth)
            .where(
                SubscribeAuth.tenant_id == tenant_id,
                SubscribeAuth.wechat_user_id == wechat_user_id,
                SubscribeAuth.template_id == template_id,
            )
            .values(available_count=SubscribeAuth.available_count + 1)
        )

    def _build_data(self, ctx: NotificationContext) -> dict:
        """微信模板字段：有精确 ``data`` 就用它，否则用 title/body 兜底（stub 用）。"""

        if ctx.payload.get("data"):
            return dict(ctx.payload["data"])
        return {
            "thing1": {"value": str(ctx.payload.get("title", ""))},
            "thing2": {"value": str(ctx.payload.get("body", ""))},
        }

    async def _degrade(self, ctx: NotificationContext, parent_id: str | UUID, reason: str) -> None:
        """降级：复用 ``in_app`` 通道的发送逻辑写一条站内信，不抛异常、不算失败。"""

        logger.warning(
            "wechat_subscribe_degraded",
            extra={
                "extra_fields": {
                    "dedupe_key": ctx.dedupe_key,
                    "parent_id": str(parent_id),
                    "reason": reason,
                }
            },
        )
        sub_payload = dict(ctx.payload)
        sub_payload["parent_ids"] = [str(parent_id)]
        sub_payload["type"] = ctx.payload.get("type", ctx.event_type)
        sub_payload["source_type"] = ctx.payload.get("source_type", ctx.event_type)
        sub_payload["title"] = ctx.payload.get("title") or "通知"
        body = ctx.payload.get("body")
        sub_payload["body"] = (
            f"{body}（微信订阅消息未送达，已降级为站内信）"
            if body
            else "微信订阅消息未送达，已降级为站内信"
        )
        await InAppChannel().send(
            NotificationContext(
                db=ctx.db,
                tenant_id=ctx.tenant_id,
                event_type=ctx.event_type,
                dedupe_key=ctx.dedupe_key,
                payload=sub_payload,
            )
        )


register_channel(CHANNEL_WECHAT_SUBSCRIBE, WechatSubscribeChannel())
