"""Role x endpoint authorization matrix and 401/403 error contracts."""

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import text

from app.api.deps import TenantDbSession
from app.api.errors import install_error_handlers
from app.api.permissions import (
    ParentScope,
    TeacherScope,
    get_parent_scope,
    get_teacher_scope,
    require_any,
    require_role,
)
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import decode_token
from tests.auth_support import seed_family, seed_staff, unique_openid, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run authorization tests",
    ),
]


def build_probe_app() -> FastAPI:
    """Endpoints that mirror matrix cells; business routes replace them in 1.6."""

    application = FastAPI()
    install_error_handlers(application)
    router = APIRouter()

    @router.get("/admin-only", dependencies=[Depends(require_role("admin"))])
    async def admin_only() -> dict[str, bool]:
        return {"ok": True}

    @router.get("/office", dependencies=[Depends(require_role("admin", "registrar"))])
    async def office() -> dict[str, bool]:
        return {"ok": True}

    @router.get("/teaching", dependencies=[Depends(require_any("teacher"))])
    async def teaching(
        scope: Annotated[TeacherScope, Depends(get_teacher_scope)],
    ) -> dict[str, object]:
        return {"class_ids": sorted(str(item) for item in scope.class_ids)}

    @router.get("/parent-bound", dependencies=[Depends(require_any("parent"))])
    async def parent_bound(
        scope: Annotated[ParentScope, Depends(get_parent_scope)],
    ) -> dict[str, object]:
        if scope.active is None:
            raise AppError(403, "TENANT_CONTEXT_REQUIRED", "请先选择孩子")
        return {"student_id": str(scope.active.student_id)}

    @router.get("/any-business")
    async def any_business(db: TenantDbSession) -> dict[str, str]:
        # Using the tenant session at all proves the tenant gate passed.
        return {"ok": "true"}

    application.include_router(router)
    return application


@dataclass(frozen=True)
class Actors:
    tokens: dict[str, str]
    parent_student_id: UUID
    unbound_token: str


@pytest.fixture
async def actors(client: httpx.AsyncClient, owner_engine) -> Actors:
    tokens: dict[str, str] = {}
    for role in ("admin", "registrar", "teacher"):
        staff = await seed_staff(
            owner_engine,
            tenant_name=f"矩阵{role}",
            role=role,
            phone=unique_phone(),
        )
        response = await client.post(
            "/auth/login", json={"phone": staff.phone, "password": staff.password}
        )
        assert response.status_code == 200, response.text
        tokens[role] = response.json()["access_token"]

    parent_openid = unique_openid("matrix-parent")
    family = await seed_family(
        owner_engine,
        entries=[("矩阵家长机构", "矩阵学生")],
        openid=parent_openid,
    )
    bound = await client.post("/auth/wechat-login", json={"wx_code": parent_openid})
    assert bound.status_code == 200, bound.text
    tokens["parent"] = bound.json()["access_token"]

    unbound = await client.post(
        "/auth/wechat-login", json={"wx_code": unique_openid("matrix-unbound")}
    )
    return Actors(
        tokens=tokens,
        parent_student_id=family.student_ids[0],
        unbound_token=unbound.json()["access_token"],
    )


async def test_role_matrix(client, actors: Actors) -> None:
    probe = build_probe_app()
    transport = httpx.ASGITransport(app=probe)
    expected = {
        "/admin-only": {"admin": 200, "registrar": 403, "teacher": 403, "parent": 403},
        "/office": {"admin": 200, "registrar": 200, "teacher": 403, "parent": 403},
        "/teaching": {"admin": 403, "registrar": 403, "teacher": 200, "parent": 403},
        "/parent-bound": {"admin": 403, "registrar": 403, "teacher": 403, "parent": 200},
        "/any-business": {"admin": 200, "registrar": 200, "teacher": 200, "parent": 200},
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://probe") as probe_client:
        for path, expectations in expected.items():
            for role, status_code in expectations.items():
                response = await probe_client.get(
                    path, headers={"Authorization": f"Bearer {actors.tokens[role]}"}
                )
                assert response.status_code == status_code, (
                    f"{path} as {role}: {response.status_code} {response.text}"
                )
                if status_code == 403:
                    assert response.json()["error"]["code"] == "OUT_OF_SCOPE"

        unbound_business = await probe_client.get(
            "/any-business",
            headers={"Authorization": f"Bearer {actors.unbound_token}"},
        )
        assert unbound_business.status_code == 403
        assert unbound_business.json()["error"]["code"] == "TENANT_CONTEXT_REQUIRED"

        unbound_bound_only = await probe_client.get(
            "/parent-bound",
            headers={"Authorization": f"Bearer {actors.unbound_token}"},
        )
        assert unbound_bound_only.status_code == 403


async def test_missing_and_tampered_tokens_are_rejected(client, actors: Actors) -> None:
    missing = await client.get("/me/students")
    tampered = await client.get(
        "/me/students",
        headers={"Authorization": f"Bearer {actors.tokens['parent'][:-2]}xx"},
    )

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "TOKEN_REQUIRED"
    assert tampered.status_code == 401
    assert tampered.json()["error"]["code"] == "TOKEN_INVALID"


async def test_expired_access_token_reports_expired(client, actors: Actors) -> None:
    settings = get_settings()
    claims = decode_token(actors.tokens["parent"], expected_type="access")
    now = datetime.now(UTC)
    expired = jwt.encode(
        {
            "sub": str(claims.subject_id),
            "tenant_id": str(claims.tenant_id),
            "role": "parent",
            "scope_type": "bound_students",
            "active_student_id": str(claims.active_student_id),
            "type": "access",
            "iat": int((now - timedelta(hours=3)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    response = await client.get("/me/students", headers={"Authorization": f"Bearer {expired}"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


async def test_forged_role_claim_is_rejected(client, actors: Actors) -> None:
    settings = get_settings()
    claims = decode_token(actors.tokens["registrar"], expected_type="access")
    forged = jwt.encode(
        {
            "sub": str(claims.subject_id),
            "tenant_id": str(claims.tenant_id),
            "role": "admin",
            "scope_type": "tenant",
            "type": "access",
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    response = await client.get("/me/students", headers={"Authorization": f"Bearer {forged}"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


async def test_tenant_claim_swap_is_rejected(client, actors: Actors) -> None:
    settings = get_settings()
    claims = decode_token(actors.tokens["admin"], expected_type="access")
    swapped = jwt.encode(
        {
            "sub": str(claims.subject_id),
            "tenant_id": str(uuid4()),
            "role": "admin",
            "scope_type": "tenant",
            "type": "access",
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    response = await client.get("/me/students", headers={"Authorization": f"Bearer {swapped}"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


async def test_guessed_subject_id_is_rejected(client) -> None:
    settings = get_settings()
    random_token = jwt.encode(
        {
            "sub": str(uuid4()),
            "tenant_id": None,
            "role": "parent",
            "scope_type": "bound_students",
            "type": "access",
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    response = await client.get("/me/students", headers={"Authorization": f"Bearer {random_token}"})

    assert response.status_code == 401


async def test_revoked_binding_takes_effect_immediately(
    client, owner_engine, actors: Actors
) -> None:
    claims = decode_token(actors.tokens["parent"], expected_type="access")
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM parent_student WHERE tenant_id = :tenant AND student_id = :student"),
            {"tenant": claims.tenant_id, "student": actors.parent_student_id},
        )

    response = await client.get(
        "/me/students",
        headers={"Authorization": f"Bearer {actors.tokens['parent']}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "OUT_OF_SCOPE"
