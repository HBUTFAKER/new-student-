"""Tenant provisioning: atomicity, idempotency and tenant isolation."""

import os
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.security import decode_token
from app.db.tenant import tenant_context
from app.main import create_app
from tests.auth_support import seed_staff, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run provisioning tests",
    ),
]


async def platform_token(client: httpx.AsyncClient, owner_engine) -> str:
    staff = await seed_staff(
        owner_engine,
        tenant_name=f"平台运营-{uuid4().hex[:8]}",
        role="platform_admin",
        phone=unique_phone(),
    )
    response = await client.post(
        "/auth/login", json={"phone": staff.phone, "password": staff.password}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def payload(name: str) -> dict[str, str]:
    return {
        "name": name,
        "admin_name": "新机构管理员",
        "admin_phone": unique_phone(),
        "admin_password": "initial-password-123",
    }


async def test_provision_creates_complete_tenant(client, owner_engine) -> None:
    token = await platform_token(client, owner_engine)
    body = payload(f"新开机构-{uuid4().hex[:8]}")

    response = await client.post(
        "/platform/tenants",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201, response.text
    result = response.json()
    tenant_id = UUID(result["tenant_id"])

    async with owner_engine.connect() as connection:
        subscription = (
            await connection.execute(
                text("SELECT plan_id, status FROM tenant_subscription WHERE tenant_id = :id"),
                {"id": tenant_id},
            )
        ).one()
        org_config = (
            await connection.execute(
                text("SELECT absence_deduct_rule FROM org_config WHERE tenant_id = :id"),
                {"id": tenant_id},
            )
        ).one()
        admin = (
            await connection.execute(
                text(
                    "SELECT id, role, status, password_hash FROM staff_account "
                    "WHERE tenant_id = :id"
                ),
                {"id": tenant_id},
            )
        ).one()
    assert subscription.status == "active"
    assert str(subscription.plan_id) == result["plan_id"]
    assert org_config.absence_deduct_rule == "none"
    assert admin.id == UUID(result["admin_staff_id"])
    assert admin.role == "admin"
    assert admin.status == "active"
    assert admin.password_hash.startswith("$argon2")

    login = await client.post(
        "/auth/login",
        json={"phone": body["admin_phone"], "password": body["admin_password"]},
    )
    assert login.status_code == 200, login.text
    assert login.json()["role"] == "admin"
    assert login.json()["tenant_id"] == str(tenant_id)


async def test_new_tenant_admin_sees_empty_own_data_only(client, owner_engine) -> None:
    token = await platform_token(client, owner_engine)
    body = payload(f"空数据机构-{uuid4().hex[:8]}")
    created = await client.post(
        "/platform/tenants", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    tenant_id = UUID(created.json()["tenant_id"])

    other_tenant, other_student = uuid4(), uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
            {"id": other_tenant, "name": f"他租户-{uuid4().hex[:8]}"},
        )
        await connection.execute(
            text("INSERT INTO student (id, tenant_id, name) VALUES (:id, :tenant, '他租户学生')"),
            {"id": other_student, "tenant": other_tenant},
        )

    engine = create_async_engine(get_settings().database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            async with tenant_context(session, tenant_id):
                own_count = await session.scalar(text("SELECT count(*) FROM student"))
            async with tenant_context(session, tenant_id):
                foreign = await session.scalar(
                    text("SELECT count(*) FROM student WHERE id = :id"),
                    {"id": other_student},
                )
    finally:
        await engine.dispose()

    assert own_count == 0
    assert foreign == 0


async def test_duplicate_name_is_409_without_half_state(client, owner_engine) -> None:
    token = await platform_token(client, owner_engine)
    body = payload(f"重复机构-{uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {token}"}

    first = await client.post("/platform/tenants", json=body, headers=headers)
    second = await client.post("/platform/tenants", json=body, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "TENANT_NAME_EXISTS"

    async with owner_engine.connect() as connection:
        counts = (
            await connection.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM tenant WHERE name = :name) AS tenants, "
                    "(SELECT count(*) FROM staff_account WHERE phone = :phone) AS staff, "
                    "(SELECT count(*) FROM org_config o JOIN tenant t ON t.id = o.tenant_id "
                    " WHERE t.name = :name) AS configs"
                ),
                {"name": body["name"], "phone": body["admin_phone"]},
            )
        ).one()
    assert counts.tenants == 1
    assert counts.staff == 1
    assert counts.configs == 1


async def test_failure_after_org_config_rolls_back_tenant(
    client, owner_engine, monkeypatch
) -> None:
    token = await platform_token(client, owner_engine)
    body = payload(f"回滚机构-{uuid4().hex[:8]}")

    async def boom(*_args, **_kwargs):
        raise RuntimeError("simulated failure after org_config")

    monkeypatch.setattr("app.services.tenant_provisioning._create_admin_account", boom)
    transport = httpx.ASGITransport(app=create_app(), raise_app_exceptions=False)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
        response = await raw.post(
            "/platform/tenants",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 500
    async with owner_engine.connect() as connection:
        remaining = await connection.scalar(
            text("SELECT count(*) FROM tenant WHERE name = :name"), {"name": body["name"]}
        )
    assert remaining == 0


async def test_only_platform_admin_can_provision(client, owner_engine) -> None:
    tenant_admin = await seed_staff(
        owner_engine,
        tenant_name=f"普通机构-{uuid4().hex[:8]}",
        role="admin",
        phone=unique_phone(),
    )
    login = await client.post(
        "/auth/login",
        json={"phone": tenant_admin.phone, "password": tenant_admin.password},
    )
    token = login.json()["access_token"]

    response = await client.post(
        "/platform/tenants",
        json=payload(f"越权机构-{uuid4().hex[:8]}"),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "OUT_OF_SCOPE"


async def test_platform_token_has_no_tenant_claim(client, owner_engine) -> None:
    token = await platform_token(client, owner_engine)

    claims = decode_token(token, expected_type="access")

    assert claims.role == "platform_admin"
    assert claims.tenant_id is None
