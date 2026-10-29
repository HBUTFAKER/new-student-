"""Authentication endpoints (0.5 section 1)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DbSession
from app.schemas.auth import (
    RefreshRequest,
    RefreshResponse,
    StaffLoginRequest,
    StaffTokenResponse,
    WechatLoginRequest,
    WechatTokenResponse,
)
from app.services.auth import login_staff, login_wechat, refresh_tokens
from app.services.wechat import WechatAuthClient, get_wechat_client

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=StaffTokenResponse, summary="员工账密登录")
async def staff_login(payload: StaffLoginRequest, db: DbSession) -> StaffTokenResponse:
    pair = await login_staff(db, payload.phone, payload.password)
    return StaffTokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        role=pair.role,
        tenant_id=pair.tenant_id,
    )


@router.post("/wechat-login", response_model=WechatTokenResponse, summary="家长微信登录")
async def wechat_login(
    payload: WechatLoginRequest,
    db: DbSession,
    client: Annotated[WechatAuthClient, Depends(get_wechat_client)],
) -> WechatTokenResponse:
    pair, bound_students = await login_wechat(db, payload.wx_code, client)
    return WechatTokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        role=pair.role,
        tenant_id=pair.tenant_id,
        active_student_id=pair.active_student_id,
        bound_students=bound_students,
    )


@router.post("/refresh", response_model=RefreshResponse, summary="刷新 access token")
async def refresh(payload: RefreshRequest, db: DbSession) -> RefreshResponse:
    pair = await refresh_tokens(db, payload.refresh_token)
    return RefreshResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )
