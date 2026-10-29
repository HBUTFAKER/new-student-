"""Authentication services: staff password login, WeChat login, token refresh."""

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal
from app.core.errors import AppError
from app.core.security import (
    TokenClaims,
    TokenExpiredError,
    TokenInvalidError,
    access_token_ttl,
    burn_password_check,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.models.wechat import WechatOpenid, WechatUser
from app.services.wechat import WechatAuthClient, WechatAuthError, WechatSession

logger = logging.getLogger(__name__)

ROLE_SCOPES = {
    "admin": "tenant",
    "registrar": "tenant",
    "teacher": "teaching_classes",
    "parent": "bound_students",
}


@dataclass(frozen=True, slots=True)
class StaffRecord:
    id: UUID
    tenant_id: UUID
    password_hash: str
    role: str
    status: str
    teacher_id: UUID | None


@dataclass(frozen=True, slots=True)
class BindingRecord:
    parent_id: UUID
    tenant_id: UUID
    student_id: UUID
    student_name: str
    relation: str | None
    tenant_name: str
    student_status: str


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    role: str
    tenant_id: UUID | None
    active_student_id: UUID | None


def _token_pair(
    *,
    subject_id: UUID,
    tenant_id: UUID | None,
    role: str,
    active_student_id: UUID | None = None,
    teacher_id: UUID | None = None,
) -> TokenPair:
    scope_type = ROLE_SCOPES[role]
    return TokenPair(
        access_token=create_access_token(
            subject_id=subject_id,
            tenant_id=tenant_id,
            role=role,
            scope_type=scope_type,
            active_student_id=active_student_id,
            teacher_id=teacher_id,
        ),
        refresh_token=create_refresh_token(
            subject_id=subject_id,
            tenant_id=tenant_id,
            role=role,
            scope_type=scope_type,
            active_student_id=active_student_id,
            teacher_id=teacher_id,
        ),
        expires_in=access_token_ttl(),
        role=role,
        tenant_id=tenant_id,
        active_student_id=active_student_id,
    )


async def staff_candidates(db: AsyncSession, phone: str) -> list[StaffRecord]:
    """Controlled cross-tenant lookup used only for password login."""

    rows = (
        await db.execute(text("SELECT * FROM auth_staff_candidates(:phone)"), {"phone": phone})
    ).mappings()
    return [
        StaffRecord(
            id=row["id"],
            tenant_id=row["tenant_id"],
            password_hash=row["password_hash"],
            role=row["role"],
            status=row["status"],
            teacher_id=row["teacher_id"],
        )
        for row in rows
    ]


async def staff_by_id(db: AsyncSession, staff_id: UUID) -> StaffRecord | None:
    row = (
        (
            await db.execute(
                text("SELECT * FROM auth_staff_by_id(:staff_id)"), {"staff_id": staff_id}
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return StaffRecord(
        id=row["id"],
        tenant_id=row["tenant_id"],
        password_hash=row["password_hash"],
        role=row["role"],
        status=row["status"],
        teacher_id=row["teacher_id"],
    )


async def wechat_bindings(db: AsyncSession, wechat_user_id: UUID) -> list[BindingRecord]:
    """The single controlled cross-tenant read for parent identity."""

    rows = (
        await db.execute(
            text("SELECT * FROM auth_wechat_bindings(:wechat_user_id)"),
            {"wechat_user_id": wechat_user_id},
        )
    ).mappings()
    return [
        BindingRecord(
            parent_id=row["parent_id"],
            tenant_id=row["tenant_id"],
            student_id=row["student_id"],
            student_name=row["student_name"],
            relation=row["relation"],
            tenant_name=row["tenant_name"],
            student_status=row["student_status"],
        )
        for row in rows
    ]


def _active_binding(
    bindings: list[BindingRecord], *, tenant_id: UUID | None, student_id: UUID | None
) -> BindingRecord | None:
    for binding in bindings:
        if binding.tenant_id == tenant_id and binding.student_id == student_id:
            return binding
    return None


async def resolve_principal(db: AsyncSession, claims: TokenClaims) -> Principal:
    """Re-read role/tenant state from the database; never trust token role."""

    if claims.role == "parent":
        exists = await db.scalar(
            text("SELECT 1 FROM wechat_user WHERE id = :id"), {"id": claims.subject_id}
        )
        if not exists:
            raise AppError(401, "TOKEN_INVALID", "登录状态无效")

        bindings = await wechat_bindings(db, claims.subject_id)
        if claims.tenant_id is None:
            if claims.active_student_id is not None:
                raise AppError(401, "TOKEN_INVALID", "登录状态无效")
            return Principal(
                subject_id=claims.subject_id,
                tenant_id=None,
                role="parent",
                scope_type=ROLE_SCOPES["parent"],
            )

        binding = _active_binding(
            bindings, tenant_id=claims.tenant_id, student_id=claims.active_student_id
        )
        if binding is None:
            raise AppError(403, "OUT_OF_SCOPE", "当前孩子不在绑定集合内")
        return Principal(
            subject_id=claims.subject_id,
            tenant_id=claims.tenant_id,
            role="parent",
            scope_type=ROLE_SCOPES["parent"],
            active_student_id=claims.active_student_id,
        )

    record = await staff_by_id(db, claims.subject_id)
    if record is None or record.status != "active":
        raise AppError(401, "TOKEN_INVALID", "登录状态无效")
    if record.role != claims.role or record.tenant_id != claims.tenant_id:
        # A signature-valid token with stale or widened claims is still rejected.
        raise AppError(401, "TOKEN_INVALID", "登录状态无效")

    return Principal(
        subject_id=record.id,
        tenant_id=record.tenant_id,
        role=record.role,
        scope_type=ROLE_SCOPES.get(record.role, "tenant"),
        teacher_id=record.teacher_id,
    )


async def login_staff(db: AsyncSession, phone: str, password: str) -> TokenPair:
    candidates = await staff_candidates(db, phone)
    if not candidates:
        burn_password_check(password)
        raise AppError(401, "AUTH_FAILED", "手机号或密码错误")

    matched = [record for record in candidates if verify_password(password, record.password_hash)]
    # A phone may exist in several tenants. Without an organisation hint the
    # login is ambiguous, so refuse rather than guess a tenant.
    if len(matched) != 1:
        if len(matched) > 1:
            logger.warning("ambiguous_staff_login", extra={"extra_fields": {"phone": phone}})
        raise AppError(401, "AUTH_FAILED", "手机号或密码错误")

    record = matched[0]
    if record.status != "active":
        raise AppError(401, "AUTH_FAILED", "账号已停用")

    return _token_pair(
        subject_id=record.id,
        tenant_id=record.tenant_id,
        role=record.role,
        teacher_id=record.teacher_id,
    )


async def _upsert_wechat_identity(
    db: AsyncSession, client: WechatAuthClient, wx_session: WechatSession
) -> WechatUser:
    mapping = (
        await db.execute(
            text(
                "SELECT wechat_user_id FROM wechat_openid "
                "WHERE app_id = :app_id AND openid = :openid"
            ),
            {"app_id": client.app_id, "openid": wx_session.openid},
        )
    ).scalar_one_or_none()

    if mapping is not None:
        user = await db.get(WechatUser, mapping)
        if user is None:
            raise AppError(401, "WX_AUTH_FAILED", "微信授权失败")
        if wx_session.unionid:
            if user.unionid is None:
                user.unionid = wx_session.unionid
            elif user.unionid != wx_session.unionid:
                # 0.6: conflicting unionid must never auto-merge accounts.
                raise AppError(401, "WX_IDENTITY_CONFLICT", "微信身份冲突，请人工核验")
        return user

    user: WechatUser | None = None
    if wx_session.unionid:
        existing_id = await db.scalar(
            text("SELECT id FROM wechat_user WHERE unionid = :unionid"),
            {"unionid": wx_session.unionid},
        )
        if existing_id is not None:
            user = await db.get(WechatUser, existing_id)

    if user is None:
        user = WechatUser(unionid=wx_session.unionid)
        try:
            async with db.begin_nested():
                db.add(user)
                await db.flush()
        except IntegrityError:
            # Concurrent first login with the same unionid: reuse the winner.
            existing_id = await db.scalar(
                text("SELECT id FROM wechat_user WHERE unionid = :unionid"),
                {"unionid": wx_session.unionid},
            )
            if existing_id is None:
                raise AppError(401, "WX_AUTH_FAILED", "微信授权失败") from None
            user = await db.get(WechatUser, existing_id)
            if user is None:
                raise AppError(401, "WX_AUTH_FAILED", "微信授权失败") from None

    try:
        async with db.begin_nested():
            db.add(
                WechatOpenid(wechat_user_id=user.id, app_id=client.app_id, openid=wx_session.openid)
            )
            await db.flush()
    except IntegrityError:
        # Concurrent first login from the same device: reuse the winning row.
        existing = (
            await db.execute(
                text(
                    "SELECT wechat_user_id FROM wechat_openid "
                    "WHERE app_id = :app_id AND openid = :openid"
                ),
                {"app_id": client.app_id, "openid": wx_session.openid},
            )
        ).scalar_one()
        user = await db.get(WechatUser, existing)
        if user is None:
            raise AppError(401, "WX_AUTH_FAILED", "微信授权失败") from None
    return user


async def login_wechat(
    db: AsyncSession, wx_code: str, client: WechatAuthClient
) -> tuple[TokenPair, int]:
    try:
        wx_session = await client.exchange(wx_code)
    except WechatAuthError as exc:
        raise AppError(401, "WX_AUTH_FAILED", "微信授权失败") from exc

    user = await _upsert_wechat_identity(db, client, wx_session)
    bindings = await wechat_bindings(db, user.id)
    distinct = {(binding.tenant_id, binding.student_id): binding for binding in bindings}

    active: BindingRecord | None = None
    if len(distinct) == 1:
        answer = next(iter(distinct.values()))
        if answer.student_status == "active":
            active = answer

    pair = _token_pair(
        subject_id=user.id,
        tenant_id=active.tenant_id if active else None,
        role="parent",
        active_student_id=active.student_id if active else None,
    )
    await db.commit()
    return pair, len(distinct)


async def refresh_tokens(db: AsyncSession, refresh_token: str) -> TokenPair:
    try:
        claims = decode_token(refresh_token, expected_type="refresh")
    except TokenExpiredError as exc:
        raise AppError(
            401, "TOKEN_EXPIRED", "登录已过期", headers={"WWW-Authenticate": "Bearer"}
        ) from exc
    except TokenInvalidError as exc:
        raise AppError(
            401, "TOKEN_INVALID", "登录状态无效", headers={"WWW-Authenticate": "Bearer"}
        ) from exc
    principal = await resolve_principal(db, claims)
    if principal.role == "parent":
        return _token_pair(
            subject_id=principal.subject_id,  # type: ignore[arg-type]
            tenant_id=principal.tenant_id,
            role="parent",
            active_student_id=principal.active_student_id,
        )
    return _token_pair(
        subject_id=principal.subject_id,  # type: ignore[arg-type]
        tenant_id=principal.tenant_id,
        role=principal.role,
        teacher_id=principal.teacher_id,
    )


async def switch_active_student(
    db: AsyncSession, principal: Principal, student_id: UUID
) -> TokenPair:
    if principal.role != "parent" or principal.subject_id is None:
        raise AppError(403, "OUT_OF_SCOPE", "仅家长可切换孩子")

    bindings = await wechat_bindings(db, principal.subject_id)
    target = next((binding for binding in bindings if binding.student_id == student_id), None)
    if target is None:
        raise AppError(403, "OUT_OF_BINDING_SET", "目标孩子不在绑定集合内")
    if target.student_status != "active":
        raise AppError(403, "OUT_OF_SCOPE", "孩子档案已归档")

    return _token_pair(
        subject_id=principal.subject_id,
        tenant_id=target.tenant_id,
        role="parent",
        active_student_id=target.student_id,
    )
