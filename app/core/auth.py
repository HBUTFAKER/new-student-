"""Injectable authentication contract (JWT implementation arrives in 1.4)."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from fastapi import Request

from app.core.config import get_settings


@dataclass(frozen=True, slots=True)
class Principal:
    """Identity information consumed by authorization and tenant context."""

    subject_id: UUID | None = None
    tenant_id: UUID | None = None
    role: str = "anonymous"
    scope_type: str = "tenant"


class PrincipalProvider(Protocol):
    """Replaceable authentication interface used by FastAPI dependencies."""

    def __call__(self, request: Request) -> Principal: ...


def get_principal(request: Request) -> Principal:
    """Return a development principal without coupling callers to JWT code.

    Debug identity headers are intentionally accepted only in local/test. The
    dependency itself is the stable seam: 1.4 replaces its implementation with
    real JWT verification without changing tenant or authorization consumers.
    """

    if get_settings().app_env not in {"local", "test"}:
        return Principal()

    raw_tenant = request.headers.get("X-Debug-Tenant-ID")
    raw_subject = request.headers.get("X-Debug-Subject-ID")
    try:
        tenant_id = UUID(raw_tenant) if raw_tenant else None
        subject_id = UUID(raw_subject) if raw_subject else None
    except ValueError:
        # An invalid injected identity is anonymous, never a valid tenant.
        return Principal()

    return Principal(
        subject_id=subject_id,
        tenant_id=tenant_id,
        role=request.headers.get("X-Debug-Role", "anonymous"),
        scope_type=request.headers.get("X-Debug-Scope-Type", "tenant"),
    )
