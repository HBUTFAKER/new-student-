"""Password hashing and JWT issuing/verification primitives."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

TokenType = Literal["access", "refresh"]

# argon2-cffi defaults (RFC 9106 recommendations) are stored inside the hash.
_password_hasher = PasswordHasher()
# Verifying this against an unknown account keeps the timing profile close to a
# real failed login and avoids a trivial phone-number oracle.
_DUMMY_HASH = _password_hasher.hash("dummy-password-for-timing-only")


def hash_password(password: str) -> str:
    """Return an argon2id hash for storage."""

    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Verify a password without leaking malformed hashes as 500s."""

    if not password_hash:
        return False
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def burn_password_check(password: str) -> None:
    """Spend the same work as a failed verification for unknown accounts."""

    verify_password(password, _DUMMY_HASH)


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Decoded JWT payload with the fields the application relies on."""

    subject_id: UUID
    tenant_id: UUID | None
    role: str
    scope_type: str
    token_type: TokenType
    active_student_id: UUID | None = None
    teacher_id: UUID | None = None


def _encode(
    *,
    subject_id: UUID,
    tenant_id: UUID | None,
    role: str,
    scope_type: str,
    token_type: TokenType,
    ttl_seconds: int,
    active_student_id: UUID | None = None,
    teacher_id: UUID | None = None,
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(subject_id),
        "tenant_id": str(tenant_id) if tenant_id else None,
        "role": role,
        "scope_type": scope_type,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    if active_student_id:
        payload["active_student_id"] = str(active_student_id)
    if teacher_id:
        payload["teacher_id"] = str(teacher_id)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(
    *,
    subject_id: UUID,
    tenant_id: UUID | None,
    role: str,
    scope_type: str,
    active_student_id: UUID | None = None,
    teacher_id: UUID | None = None,
) -> str:
    return _encode(
        subject_id=subject_id,
        tenant_id=tenant_id,
        role=role,
        scope_type=scope_type,
        token_type="access",
        ttl_seconds=get_settings().access_token_ttl_seconds,
        active_student_id=active_student_id,
        teacher_id=teacher_id,
    )


def create_refresh_token(
    *,
    subject_id: UUID,
    tenant_id: UUID | None,
    role: str,
    scope_type: str,
    active_student_id: UUID | None = None,
    teacher_id: UUID | None = None,
) -> str:
    return _encode(
        subject_id=subject_id,
        tenant_id=tenant_id,
        role=role,
        scope_type=scope_type,
        token_type="refresh",
        ttl_seconds=get_settings().refresh_token_ttl_seconds,
        active_student_id=active_student_id,
        teacher_id=teacher_id,
    )


class TokenExpiredError(Exception):
    """Raised when a token signature is valid but its expiry has passed."""


class TokenInvalidError(Exception):
    """Raised for malformed tokens, bad signatures or the wrong token type."""


def decode_token(token: str, *, expected_type: TokenType) -> TokenClaims:
    """Verify signature/expiry and return typed claims."""

    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError from exc
    except jwt.InvalidTokenError as exc:
        raise TokenInvalidError from exc

    if payload.get("type") != expected_type:
        raise TokenInvalidError

    try:
        subject_id = UUID(payload["sub"])
        tenant_id = UUID(payload["tenant_id"]) if payload.get("tenant_id") else None
        active_student_id = (
            UUID(payload["active_student_id"]) if payload.get("active_student_id") else None
        )
        teacher_id = UUID(payload["teacher_id"]) if payload.get("teacher_id") else None
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenInvalidError from exc

    return TokenClaims(
        subject_id=subject_id,
        tenant_id=tenant_id,
        role=str(payload.get("role", "")),
        scope_type=str(payload.get("scope_type", "")),
        token_type=expected_type,
        active_student_id=active_student_id,
        teacher_id=teacher_id,
    )


def access_token_ttl() -> int:
    return get_settings().access_token_ttl_seconds
