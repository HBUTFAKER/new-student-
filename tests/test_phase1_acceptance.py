"""Phase 1 acceptance: two tenants, three attack vectors, zero leakage (step 1.6).

The three vectors are the ones named in the order: swap resource ids in the URL,
forge a token, and walk guessed ids. Every attempt must end in an empty result
set, 403 or 404 -- never in another tenant's row and never in a 500.
"""

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.security import decode_token, hash_password
from tests.auth_support import unique_openid, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run acceptance tests",
    ),
]

PASSWORD = "acceptance-password"


@dataclass(frozen=True, slots=True)
class TenantState:
    """Everything an attacker needs to aim at one tenant."""

    label: str
    tenant_id: UUID
    admin_token: str
    student_id: UUID
    student_name: str
    parent_id: UUID
    binding_id: UUID


async def _provision(client: httpx.AsyncClient, owner_engine, label: str) -> TenantState:
    """Create a tenant plus its admin, then build data through the public API."""

    tenant_id, phone = uuid4(), unique_phone()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
            {"id": tenant_id, "name": f"{label}机构-{uuid4().hex[:8]}"},
        )
        await connection.execute(
            text(
                "INSERT INTO staff_account "
                "(id, tenant_id, name, phone, password_hash, role, status) "
                "VALUES (:id, :tenant, :name, :phone, :hash, 'admin', 'active')"
            ),
            {
                "id": uuid4(),
                "tenant": tenant_id,
                "name": f"{label}管理员",
                "phone": phone,
                "hash": hash_password(PASSWORD),
            },
        )

    login = await client.post("/auth/login", json={"phone": phone, "password": PASSWORD})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    student = await client.post(
        "/students", json={"name": f"{label}学员", "grade": "三年级"}, headers=headers
    )
    assert student.status_code == 201, student.text

    parent = await client.post(
        "/parents",
        json={"name": f"{label}家长", "phone": unique_phone()},
        headers=headers,
    )
    assert parent.status_code == 201, parent.text

    binding = await client.post(
        "/bindings",
        json={"parent_id": parent.json()["id"], "student_id": student.json()["id"]},
        headers=headers,
    )
    assert binding.status_code == 201, binding.text

    return TenantState(
        label=label,
        tenant_id=tenant_id,
        admin_token=login.json()["access_token"],
        student_id=UUID(student.json()["id"]),
        student_name=student.json()["name"],
        parent_id=UUID(parent.json()["id"]),
        binding_id=UUID(binding.json()["id"]),
    )


@pytest.fixture
async def tenants(client: httpx.AsyncClient, owner_engine) -> tuple[TenantState, TenantState]:
    alpha = await _provision(client, owner_engine, "甲")
    beta = await _provision(client, owner_engine, "乙")

    # The ids really do live in different tenants; otherwise the test is theatre.
    assert alpha.tenant_id != beta.tenant_id
    return alpha, beta


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _forge(claims: dict[str, object], *, secret: str | None = None) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "role": "admin",
        "scope_type": "tenant",
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    } | claims
    key = secret if secret is not None else settings.jwt_secret_key
    return jwt.encode(payload, key, algorithm=settings.jwt_algorithm)


# ------------------------------------------------------- baseline: own data only


async def test_each_tenant_only_sees_its_own_records(
    client: httpx.AsyncClient, tenants: tuple[TenantState, TenantState]
) -> None:
    alpha, beta = tenants

    students = await client.get("/students", headers=_bearer(alpha.admin_token))
    assert students.status_code == 200
    assert students.json()["total"] == 1
    assert [item["id"] for item in students.json()["items"]] == [str(alpha.student_id)]

    searched = await client.get(
        "/students", params={"name": beta.student_name}, headers=_bearer(alpha.admin_token)
    )
    assert searched.status_code == 200
    assert searched.json()["items"] == []
    assert searched.json()["total"] == 0

    parents = await client.get("/parents", headers=_bearer(alpha.admin_token))
    assert parents.status_code == 200
    assert [item["id"] for item in parents.json()["items"]] == [str(alpha.parent_id)]

    binds = await client.get("/bindings", headers=_bearer(alpha.admin_token))
    assert binds.status_code == 200
    assert binds.json()["total"] == 1
    assert binds.json()["items"][0]["student_id"] == str(alpha.student_id)


# -------------------------------------------------- vector 1: swap ids in the URL


async def test_swapping_url_ids_returns_not_found(
    client: httpx.AsyncClient, tenants: tuple[TenantState, TenantState]
) -> None:
    alpha, beta = tenants
    headers = _bearer(alpha.admin_token)

    cross_read = await client.get(f"/students/{beta.student_id}", headers=headers)
    assert cross_read.status_code == 404
    assert cross_read.json()["error"]["code"] == "STUDENT_NOT_FOUND"

    cross_write = await client.patch(
        f"/students/{beta.student_id}", json={"name": "被改名"}, headers=headers
    )
    assert cross_write.status_code == 404

    cross_invite = await client.post(
        "/invite-codes",
        json={"student_id": str(beta.student_id)},
        headers=headers,
    )
    assert cross_invite.status_code == 404

    cross_history = await client.get(f"/students/{beta.student_id}/invite-codes", headers=headers)
    assert cross_history.status_code == 404

    # Mixed pairs: a row from each tenant must not be joinable across the fence.
    foreign_student = await client.post(
        "/bindings",
        json={"parent_id": str(alpha.parent_id), "student_id": str(beta.student_id)},
        headers=headers,
    )
    assert foreign_student.status_code == 404
    assert foreign_student.json()["error"]["code"] == "STUDENT_NOT_FOUND"

    foreign_parent = await client.post(
        "/bindings",
        json={"parent_id": str(beta.parent_id), "student_id": str(alpha.student_id)},
        headers=headers,
    )
    assert foreign_parent.status_code == 404
    assert foreign_parent.json()["error"]["code"] == "PARENT_NOT_FOUND"

    cross_unbind = await client.delete(f"/bindings/{beta.binding_id}", headers=headers)
    assert cross_unbind.status_code == 404

    filtered = await client.get(
        "/bindings", params={"student_id": str(beta.student_id)}, headers=headers
    )
    assert filtered.status_code == 200
    assert filtered.json()["items"] == []

    # The failed PATCH must not have touched the other tenant's row.
    beta_view = await client.get(f"/students/{beta.student_id}", headers=_bearer(beta.admin_token))
    assert beta_view.status_code == 200
    assert beta_view.json()["name"] == beta.student_name


# --------------------------------------------------------- vector 2: fake tokens


async def test_forged_tokens_are_rejected(
    client: httpx.AsyncClient, tenants: tuple[TenantState, TenantState]
) -> None:
    alpha, beta = tenants
    claims = decode_token(alpha.admin_token, expected_type="access")

    tamper_suffix = "bbbb" if alpha.admin_token.endswith("aaaa") else "aaaa"
    tampered = alpha.admin_token[:-4] + tamper_suffix
    cases = {
        "broken_signature": tampered,
        "wrong_secret": _forge(
            {"sub": str(claims.subject_id), "tenant_id": str(alpha.tenant_id)},
            secret="attacker-secret",
        ),
        "unsigned": jwt.encode(
            {
                "sub": str(claims.subject_id),
                "tenant_id": str(alpha.tenant_id),
                "role": "admin",
                "scope_type": "tenant",
                "type": "access",
                "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            },
            key="",
            algorithm="none",
        ),
        # Valid signature, but the payload asks for another tenant: the server
        # re-reads the account and must refuse the swap.
        "tenant_swap": _forge({"sub": str(claims.subject_id), "tenant_id": str(beta.tenant_id)}),
        # Valid signature, escalated role that the account does not hold.
        "role_escalation": _forge(
            {
                "sub": str(claims.subject_id),
                "tenant_id": str(alpha.tenant_id),
                "role": "platform_admin",
            },
        ),
        # A refresh token is not a bearer for business routes.
        "refresh_as_access": _forge(
            {"sub": str(claims.subject_id), "tenant_id": str(alpha.tenant_id), "type": "refresh"}
        ),
    }

    for name, token in cases.items():
        response = await client.get("/students", headers=_bearer(token))
        assert response.status_code == 401, f"{name} was accepted: {response.text}"
        assert response.json()["error"]["code"] in {"TOKEN_INVALID", "TOKEN_EXPIRED"}, name

    # No usable token at all.
    anonymous = await client.get("/students")
    assert anonymous.status_code == 401

    # And the legitimate token still works, so the rejections above are real.
    assert (await client.get("/students", headers=_bearer(alpha.admin_token))).status_code == 200


# -------------------------------------------------------- vector 3: guessing ids


async def test_guessed_ids_always_come_back_empty(
    client: httpx.AsyncClient, tenants: tuple[TenantState, TenantState]
) -> None:
    alpha, _beta = tenants
    headers = _bearer(alpha.admin_token)
    guesses = [uuid4() for _ in range(12)]

    for guessed in guesses:
        detail = await client.get(f"/students/{guessed}", headers=headers)
        assert detail.status_code == 404
        assert detail.json()["error"]["code"] == "STUDENT_NOT_FOUND"

        history = await client.get(f"/students/{guessed}/invite-codes", headers=headers)
        assert history.status_code == 404

        unbind = await client.delete(f"/bindings/{guessed}", headers=headers)
        assert unbind.status_code == 404

        invite = await client.post(
            "/invite-codes", json={"student_id": str(guessed)}, headers=headers
        )
        assert invite.status_code == 404

        update = await client.patch(f"/students/{guessed}", json={"name": "猜的"}, headers=headers)
        assert update.status_code == 404

    # Malformed ids must be a validation error, not a 500 from the driver.
    malformed = await client.get("/students/not-a-uuid", headers=headers)
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "VALIDATION_ERROR"


# ------------------------------------------------- parent side: bound set is a fence


async def test_parent_sees_only_bound_children(
    client: httpx.AsyncClient, owner_engine, tenants: tuple[TenantState, TenantState]
) -> None:
    alpha, beta = tenants

    openid = unique_openid("acceptance-parent")
    first = await client.post("/auth/wechat-login", json={"wx_code": openid})
    assert first.status_code == 200, first.text
    assert first.json()["tenant_id"] is None
    wechat_user_id = decode_token(first.json()["access_token"], expected_type="access").subject_id

    # Unbound parent: no tenant context, business routes are closed.
    blocked = await client.get(
        f"/students/{alpha.student_id}", headers=_bearer(first.json()["access_token"])
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "TENANT_CONTEXT_REQUIRED"

    created = await client.post(
        "/parents",
        json={"name": "验收家长", "phone": unique_phone(), "wechat_user_id": str(wechat_user_id)},
        headers=_bearer(alpha.admin_token),
    )
    assert created.status_code == 201, created.text
    bound = await client.post(
        "/bindings",
        json={"parent_id": created.json()["id"], "student_id": str(alpha.student_id)},
        headers=_bearer(alpha.admin_token),
    )
    assert bound.status_code == 201

    login = await client.post("/auth/wechat-login", json={"wx_code": openid})
    assert login.status_code == 200
    assert login.json()["tenant_id"] == str(alpha.tenant_id)
    parent_headers = _bearer(login.json()["access_token"])

    mine = await client.get("/me/students", headers=parent_headers)
    assert mine.status_code == 200
    assert [item["student_id"] for item in mine.json()["items"]] == [str(alpha.student_id)]
    assert mine.json()["items"][0]["active"] is True

    own_child = await client.get(f"/students/{alpha.student_id}", headers=parent_headers)
    assert own_child.status_code == 200

    # Another tenant's child, and a guess, are both invisible to the parent.
    other_child = await client.get(f"/students/{beta.student_id}", headers=parent_headers)
    assert other_child.status_code == 404
    guessed_child = await client.get(f"/students/{uuid4()}", headers=parent_headers)
    assert guessed_child.status_code == 404

    # Unbinding closes the door on the very next request: no cached scope.
    removed = await client.delete(
        f"/bindings/{bound.json()['id']}", headers=_bearer(alpha.admin_token)
    )
    assert removed.status_code == 204
    after = await client.get("/me/students", headers=parent_headers)
    assert after.status_code == 403
    assert after.json()["error"]["code"] == "OUT_OF_SCOPE"


async def test_switching_child_cannot_escape_binding_set(
    client: httpx.AsyncClient, tenants: tuple[TenantState, TenantState]
) -> None:
    alpha, beta = tenants
    openid = unique_openid("acceptance-switch")
    first = await client.post("/auth/wechat-login", json={"wx_code": openid})
    wechat_user_id = decode_token(first.json()["access_token"], expected_type="access").subject_id

    created = await client.post(
        "/parents",
        json={"name": "切换家长", "phone": unique_phone(), "wechat_user_id": str(wechat_user_id)},
        headers=_bearer(alpha.admin_token),
    )
    await client.post(
        "/bindings",
        json={"parent_id": created.json()["id"], "student_id": str(alpha.student_id)},
        headers=_bearer(alpha.admin_token),
    )
    login = await client.post("/auth/wechat-login", json={"wx_code": openid})
    parent_headers = _bearer(login.json()["access_token"])

    foreign = await client.post(
        "/me/active-student", json={"student_id": str(beta.student_id)}, headers=parent_headers
    )
    assert foreign.status_code == 403
    assert foreign.json()["error"]["code"] == "OUT_OF_BINDING_SET"

    missing = await client.post(
        "/me/active-student", json={"student_id": str(uuid4())}, headers=parent_headers
    )
    assert missing.status_code == 403
