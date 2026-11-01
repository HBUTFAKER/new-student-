"""管理端短信用量查询（6.3 / GET /admin/sms-usage）。

覆盖：admin 200 且返回当日已发条数 + 预算 + 剩余、teacher 403、家长 403、
跨租户 tenant_id 404（不是 403）。
"""

import os
from dataclasses import dataclass
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text

from app.core.security import hash_password
from tests.auth_support import seed_family, unique_openid, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run admin sms usage tests",
    ),
]

PASSWORD = "admin-sms-usage-password"


@dataclass(frozen=True, slots=True)
class Seed:
    tenant_id: UUID
    other_tenant_id: UUID
    admin_phone: str
    teacher_phone: str


async def add_staff(engine, *, tenant_id: UUID, role: str) -> str:
    phone = unique_phone()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO staff_account "
                "(id, tenant_id, name, phone, password_hash, role, status) "
                "VALUES (gen_random_uuid(), :t, :name, :phone, :hash, :role, 'active')"
            ),
            {
                "t": tenant_id,
                "name": f"{role} user",
                "phone": phone,
                "hash": hash_password(PASSWORD),
                "role": role,
            },
        )
    return phone


async def login_staff(client: httpx.AsyncClient, phone: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"phone": phone, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def login_parent(client: httpx.AsyncClient, openid: str) -> dict[str, str]:
    response = await client.post("/auth/wechat-login", json={"wx_code": openid})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def insert_sms_success(engine, *, tenant_id: UUID, dedupe_key: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO notification "
                "(tenant_id, event_type, dedupe_key, status, succeeded_channels, updated_at) "
                "VALUES (:t, 'x', :k, 'succeeded', ARRAY['sms'], now())"
            ),
            {"t": tenant_id, "k": dedupe_key},
        )


@pytest.fixture(autouse=True)
async def clean_slate(owner_engine):
    """清空出站队列 / 机构配置，避免 count 到别的测试留下的 sms 成功行。"""

    async with owner_engine.begin() as connection:
        await connection.execute(text("DELETE FROM in_app_message"))
        await connection.execute(text("DELETE FROM notification"))
        await connection.execute(text("DELETE FROM org_config"))
    yield


@pytest.fixture
async def seed(owner_engine) -> Seed:
    """机构 A（管理员/老师）+ 机构 B（跨租户 target）。"""

    family = await seed_family(owner_engine, entries=[("短信机构A", "小明")])
    other_family = await seed_family(owner_engine, entries=[("短信机构B", "小红")])
    tenant_id = family.tenant_ids[0]
    return Seed(
        tenant_id=tenant_id,
        other_tenant_id=other_family.tenant_ids[0],
        admin_phone=await add_staff(owner_engine, tenant_id=tenant_id, role="admin"),
        teacher_phone=await add_staff(owner_engine, tenant_id=tenant_id, role="teacher"),
    )


async def test_admin_reads_usage(client, owner_engine, seed: Seed) -> None:
    headers = await login_staff(client, seed.admin_phone)
    await insert_sms_success(owner_engine, tenant_id=seed.tenant_id, dedupe_key="u-1")
    await insert_sms_success(owner_engine, tenant_id=seed.tenant_id, dedupe_key="u-2")

    response = await client.get("/admin/sms-usage", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["used"] == 2
    assert data["budget"] == 50  # 无 org_config 行 → 默认预算
    assert data["remaining"] == 48


async def test_teacher_cannot_read(client, owner_engine, seed: Seed) -> None:
    headers = await login_staff(client, seed.teacher_phone)
    response = await client.get("/admin/sms-usage", headers=headers)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "OUT_OF_SCOPE"


async def test_parent_cannot_read(client, owner_engine, seed: Seed) -> None:
    headers = await login_parent(client, unique_openid("sms-usage-lonely"))
    response = await client.get("/admin/sms-usage", headers=headers)
    assert response.status_code == 403, response.text


async def test_cross_tenant_usage_is_404(client, owner_engine, seed: Seed) -> None:
    headers = await login_staff(client, seed.admin_phone)
    response = await client.get(
        "/admin/sms-usage", params={"tenant_id": str(seed.other_tenant_id)}, headers=headers
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "TENANT_NOT_FOUND"
