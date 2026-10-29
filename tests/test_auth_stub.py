"""Tests for the replaceable pre-JWT principal provider."""

from types import SimpleNamespace
from uuid import uuid4

from starlette.requests import Request

from app.core import auth


def request_with_headers(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        }
    )


def test_debug_principal_is_injectable_in_local_or_test() -> None:
    tenant_id, subject_id = uuid4(), uuid4()
    request = request_with_headers(
        {
            "X-Debug-Tenant-ID": str(tenant_id),
            "X-Debug-Subject-ID": str(subject_id),
            "X-Debug-Role": "admin",
        }
    )

    principal = auth.get_principal(request)

    assert principal.tenant_id == tenant_id
    assert principal.subject_id == subject_id
    assert principal.role == "admin"


def test_malformed_debug_identity_becomes_anonymous() -> None:
    principal = auth.get_principal(request_with_headers({"X-Debug-Tenant-ID": "not-a-uuid"}))

    assert principal.tenant_id is None
    assert principal.role == "anonymous"


def test_debug_headers_are_ignored_outside_local_and_test(monkeypatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="production"))
    principal = auth.get_principal(request_with_headers({"X-Debug-Tenant-ID": str(uuid4())}))

    assert principal == auth.Principal()
