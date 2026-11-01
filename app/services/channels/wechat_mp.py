"""服务号模板消息通道（6.3）：把 6.1 通知中心引擎接到服务号 ``template/send``。

与 6.2 ``wechat_subscribe`` 的关键区别：本通道**不写站内信兜底**，未关注/未配置/超时等
永久性失败一律 ``raise ChannelSkipped`` 让降级链继续下一级（短信）；临时失败才抛普通异常
让 6.1 重试。这正是 0.7 降级链里「服务号 → 短信」这一级的语义。

裁决（发令部已定，照做不讨论）：

* **未关注公众号 = 永久失败 → 跳过本级**（不是通知失败）。服务号侧「未关注」由
  ``template/send`` 返回 43004 判定；没有服务号 appid 下的 openid 映射也视为未触达、跳过。
* **机构未配置服务号**（env 无 ``WECHAT_MP_APP_ID``）= 通道整体不可用 → 静默跳过
  （``CHANNEL_NOT_CONFIGURED``）。
* **TIMEOUT_UNKNOWN**：请求已发出但超时/无法确认上游是否接收 → 不重试同一通道，直接
  ``ChannelSkipped("TIMEOUT_UNKNOWN")`` 跳到下一级。
* 服务号 appid 与小程序 appid 是**两个**：openid 按 ``wechat_mp_app_id`` 反查，不落日志、
  不进 payload（06 红线）。

``payload`` 约定（6.4 接事件时按此填）：

* ``parent_ids``: ``[uuid, ...]`` —— 必填，收信家长；
* ``title`` / ``body``: 文案（服务号模板字段兜底用）；
* ``data``: 可选，服务号模板字段的精确映射（生产真模板必须给，stub 可省）。

已知边界（记入 08 §0）：多家长事件里某家长临时失败会重发已成功家长，6.4 按家长拆分
enqueue 解决，本步不做。
"""

from __future__ import annotations

import logging
import time
from typing import Protocol
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.archive import Parent
from app.models.wechat import WechatOpenid
from app.services.notification_center import (
    ChannelSkipped,
    NotificationContext,
    register_channel,
)

logger = logging.getLogger(__name__)

CHANNEL_WECHAT_MP = "wechat_mp"

#: 服务号侧**永久**错误码：命中即「跳过本级继续下一通道」（不重试）。43004 用户未关注/
#: 需关注后接收、40003 openid 无效、40037 模板 id 无效。
PERMANENT_ERRCODES: frozenset[int] = frozenset({43004, 40003, 40037})

TEMPLATE_MESSAGE_URL = "https://api.weixin.qq.com/cgi-bin/message/template/send"
ACCESS_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"


class WechatMpError(Exception):
    """一次服务号发送失败。``errcode`` 为 ``None`` 且 ``timeout=True`` 表示结果未知
    （TIMEOUT_UNKNOWN）；``errcode`` 命中 ``PERMANENT_ERRCODES`` 表示永久失败；其余为临时。
    """

    def __init__(self, errcode: int | None, message: str = "", *, timeout: bool = False) -> None:
        super().__init__(message or f"wechat mp template send failed (errcode={errcode})")
        self.errcode = errcode
        self.timeout = timeout


class WechatMpClient(Protocol):
    """可替换的 ``template/send`` 客户端（与 6.2 ``WechatSubscribeClient`` 同法）。"""

    async def send(self, openid: str, template_id: str, data: dict) -> None: ...


class StubWechatMpClient:
    """默认离线实现：记录每次调用，测试注入「下次抛异常 / 永久错误码 / 超时」钩子。

    ``fail_next_with`` 触发临时失败（通道抛普通异常让 6.1 重试）；``reject_next_with`` 触发
    永久失败（通道 ``ChannelSkipped`` 继续下一级）；``timeout_next`` 触发 TIMEOUT_UNKNOWN。
    钩子一次性。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.fail_next_with: Exception | None = None
        self.reject_next_with: int | None = None
        self.timeout_next: bool = False

    async def send(self, openid: str, template_id: str, data: dict) -> None:
        self.calls.append((openid, template_id, data))
        if self.fail_next_with is not None:
            exc, self.fail_next_with = self.fail_next_with, None
            raise exc
        if self.reject_next_with is not None:
            errcode, self.reject_next_with = self.reject_next_with, None
            raise WechatMpError(errcode)
        if self.timeout_next:
            self.timeout_next = False
            raise WechatMpError(None, "request timed out", timeout=True)


class RealWechatMpClient:
    """生产实现：access_token 带缓存（提前 60s 过期），调 ``template/send``。

    默认**不启用**——``build_wechat_mp_client`` 在 stub mode / local / test 下回落到 stub。
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
        except httpx.TimeoutException as exc:
            raise WechatMpError(None, "get access_token timed out", timeout=True) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise WechatMpError(None, "get access_token failed") from exc
        token = payload.get("access_token")
        if not token:
            raise WechatMpError(payload.get("errcode"), "get access_token rejected")
        self._token = token
        self._token_expires_at = now + int(payload.get("expires_in", 7200)) - 60
        return token

    async def send(self, openid: str, template_id: str, data: dict) -> None:
        try:
            token = await self._access_token()
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    TEMPLATE_MESSAGE_URL,
                    params={"access_token": token},
                    json={
                        "touser": openid,
                        "template_id": template_id,
                        "data": data,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            raise WechatMpError(None, "template send timed out", timeout=True) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise WechatMpError(None, "template send request failed") from exc
        errcode = payload.get("errcode")
        if errcode != 0:
            raise WechatMpError(errcode)


def build_wechat_mp_client(settings: Settings) -> WechatMpClient:
    """按环境选客户端：stub/local/test 走离线 stub，生产走真实现。"""

    if settings.wechat_stub_mode or settings.app_env in {"local", "test"}:
        return StubWechatMpClient()
    if not settings.wechat_mp_app_secret:
        raise RuntimeError("WECHAT_MP_APP_SECRET is required when stub mode is disabled")
    return RealWechatMpClient(settings.wechat_mp_app_id, settings.wechat_mp_app_secret)


class WechatMpChannel:
    """6.1 的通道适配器：服务号模板消息。永久失败 = ``ChannelSkipped`` 继续下一级。

    ``client`` 缺省按环境构建；测试注入 ``StubWechatMpClient`` 覆盖。``settings`` 缺省读
    ``get_settings()``（服务号 appid / 模板 id 从它拿）。
    """

    def __init__(
        self,
        client: WechatMpClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._client = client
        self._settings = settings

    def _settings_for(self) -> Settings:
        return self._settings or get_settings()

    def _client_for(self) -> WechatMpClient:
        return self._client or build_wechat_mp_client(self._settings_for())

    def _configured(self) -> bool:
        return bool(self._settings_for().wechat_mp_app_id)

    async def send(self, ctx: NotificationContext) -> None:
        parent_ids = ctx.payload.get("parent_ids")
        if not parent_ids:
            raise ValueError("wechat_mp payload 缺 parent_ids")
        if not self._configured():
            raise ChannelSkipped("CHANNEL_NOT_CONFIGURED")

        template_id = self._settings_for().wechat_mp_template_id
        client = self._client_for()
        sent_any = False
        for parent_id in parent_ids:
            try:
                await self._send_to_parent(ctx, parent_id, template_id, client)
                sent_any = True
            except ChannelSkipped:
                continue  # 该家长本级跳过（未关注等），继续别的家长
        if not sent_any:
            raise ChannelSkipped("NOT_FOLLOWING_SERVICE_ACCOUNT")

    async def _send_to_parent(
        self,
        ctx: NotificationContext,
        parent_id: str | UUID,
        template_id: str,
        client: WechatMpClient,
    ) -> None:
        wechat_user_id = await self._resolve_wechat_user_id(ctx.db, parent_id)
        if wechat_user_id is None:
            raise ChannelSkipped("NOT_FOLLOWING_SERVICE_ACCOUNT")
        openid = await self._resolve_openid(
            ctx.db, wechat_user_id, self._settings_for().wechat_mp_app_id
        )
        if openid is None:
            raise ChannelSkipped("NOT_FOLLOWING_SERVICE_ACCOUNT")

        data = self._build_data(ctx)
        try:
            await client.send(openid, template_id, data)
        except WechatMpError as exc:
            if exc.timeout:
                logger.warning(
                    "timeout_unknown",
                    extra={
                        "extra_fields": {
                            "channel": CHANNEL_WECHAT_MP,
                            "dedupe_key": ctx.dedupe_key,
                        }
                    },
                )
                raise ChannelSkipped("TIMEOUT_UNKNOWN") from exc
            if exc.errcode in PERMANENT_ERRCODES:
                raise ChannelSkipped(f"WX_{exc.errcode}") from exc
            raise  # 临时失败：抛异常让 6.1 重试

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

    def _build_data(self, ctx: NotificationContext) -> dict:
        """服务号模板字段：有精确 ``data`` 就用它，否则 title/body 兜底（stub 用）。"""

        if ctx.payload.get("data"):
            return dict(ctx.payload["data"])
        return {
            "first": {"value": str(ctx.payload.get("title", ""))},
            "remark": {"value": str(ctx.payload.get("body", ""))},
        }


register_channel(CHANNEL_WECHAT_MP, WechatMpChannel())
