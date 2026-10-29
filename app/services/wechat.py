"""Replaceable WeChat jscode2session client."""

from dataclasses import dataclass
from typing import Protocol

import httpx
from fastapi import Depends

from app.core.config import Settings, get_settings

JSCODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"


@dataclass(frozen=True, slots=True)
class WechatSession:
    openid: str
    unionid: str | None = None


class WechatAuthError(Exception):
    """The code exchange failed or returned an unusable payload."""


class WechatAuthClient(Protocol):
    app_id: str

    async def exchange(self, wx_code: str) -> WechatSession: ...


class StubWechatAuthClient:
    """Deterministic local/test client; no real appid or network access."""

    app_id = "wx_platform_stub"

    async def exchange(self, wx_code: str) -> WechatSession:
        code = wx_code.strip()
        if not code:
            raise WechatAuthError("empty wx_code")
        openid, _, unionid = code.partition("|")
        return WechatSession(openid=openid, unionid=unionid or None)


class RealWechatAuthClient:
    """Calls the official endpoint; appid/secret never leave the server."""

    def __init__(self, app_id: str, app_secret: str) -> None:
        self.app_id = app_id
        self._app_secret = app_secret

    async def exchange(self, wx_code: str) -> WechatSession:
        params = {
            "appid": self.app_id,
            "secret": self._app_secret,
            "js_code": wx_code,
            "grant_type": "authorization_code",
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(JSCODE2SESSION_URL, params=params)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WechatAuthError("jscode2session request failed") from exc

        openid = payload.get("openid")
        if not openid:
            raise WechatAuthError(f"jscode2session rejected code: {payload.get('errcode')}")
        return WechatSession(openid=openid, unionid=payload.get("unionid"))


def build_wechat_client(settings: Settings) -> WechatAuthClient:
    if settings.wechat_stub_mode or settings.app_env in {"local", "test"}:
        return StubWechatAuthClient()
    if not settings.wechat_app_secret:
        raise RuntimeError("WECHAT_APP_SECRET is required when stub mode is disabled")
    return RealWechatAuthClient(settings.wechat_app_id, settings.wechat_app_secret)


def get_wechat_client() -> WechatAuthClient:
    """FastAPI dependency; tests override it with a scripted client."""

    return build_wechat_client(get_settings())


WechatClient = Depends(get_wechat_client)
