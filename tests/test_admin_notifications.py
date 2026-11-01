"""管理端测试发送接口（6.2 / POST /admin/notifications/test）。

覆盖：admin 200 且落一条 pending 通知、故意不幂等（两次两个 id）、teacher 403、
跨租户家长 404（不是 403）、家长 403。
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
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run admin notification tests",
    ),
]

PASSWORD = "admin-notify-password"


@dataclass(frozen=True, slots=True)
class Seed:
    tenant_id: UUID
    parent_id: UUID
    other_parent_id: UUID
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


async def parent_id_for(engine, *, tenant_id: UUID, wechat_user_id: UUID) -> UUID:
    async with engine.connect() as connection:
        return await connection.scalar(
            text("SELECT id FROM parent WHERE tenant_id = :t AND wechat_user_id = :u"),
            {"t": tenant_id, "u": wechat_user_id},
        )


async def fetch_notification(engine, notification_id: UUID) -> dict:
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text("SELECT status, tenant_id, dedupe_key FROM notification WHERE id = :id"),
                    {"id": notification_id},
                )
            )
            .mappings()
            .one()
        )
    return dict(row)


@pytest.fixture(autouse=True)
async def clean_slate(owner_engine):
    """清空出站队列，避免 count 到上一个测试留下的 test 通知。"""

    async with owner_engine.begin() as connection:
        await connection.execute(text("DELETE FROM in_app_message"))
        await connection.execute(text("DELETE FROM notification"))
    yield


@pytest.fixture
async def seed(owner_engine) -> Seed:
    """机构 A（管理员/老师 + 一个家长）+ 机构 B（一个跨租户家长）。"""

    openid = unique_openid("adm-parent")
    family = await seed_family(owner_engine, entries=[("通知机构A", "小明")], openid=openid)
    other_openid = unique_openid("adm-other")
    other_family = await seed_family(
        owner_engine, entries=[("通知机构B", "小红")], openid=other_openid
    )

    tenant_id = family.tenant_ids[0]
    parent_id = await parent_id_for(
        owner_engine, tenant_id=tenant_id, wechat_user_id=family.wechat_user_id
    )
    other_parent_id = await parent_id_for(
        owner_engine,
        tenant_id=other_family.tenant_ids[0],
        wechat_user_id=other_family.wechat_user_id,
    )
    return Seed(
        tenant_id=tenant_id,
        parent_id=parent_id,
        other_parent_id=other_parent_id,
        admin_phone=await add_staff(owner_engine, tenant_id=tenant_id, role="admin"),
        teacher_phone=await add_staff(owner_engine, tenant_id=tenant_id, role="teacher"),
    )


async def test_admin_can_send_test_notification(client, owner_engine, seed: Seed) -> None:
    headers = await login_staff(client, seed.admin_phone)
    response = await client.post(
        "/admin/notifications/test",
        json={"parent_id": str(seed.parent_id), "template_scene": "lesson_remind"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    notification_id = UUID(response.json()["notification_id"])
    row = await fetch_notification(owner_engine, notification_id)
    assert row["status"] == "pending"
    assert str(row["tenant_id"]) == str(seed.tenant_id)
    assert row["dedupe_key"].startswith("test:")


async def test_test_notification_is_deliberately_not_idempotent(
    client, owner_engine, seed: Seed
) -> None:
    headers = await login_staff(client, seed.admin_phone)
    payload = {"parent_id": str(seed.parent_id), "template_scene": "lesson_remind"}

    first = await client.post("/admin/notifications/test", json=payload, headers=headers)
    second = await client.post("/admin/notifications/test", json=payload, headers=headers)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    assert first.json()["notification_id"] != second.json()["notification_id"]
    async with owner_engine.connect() as connection:
        total = int(
            await connection.scalar(
                text("SELECT count(*) FROM notification WHERE dedupe_key LIKE 'test:%'")
            )
            or 0
        )
    assert total == 2


async def test_teacher_cannot_send(client, owner_engine, seed: Seed) -> None:
    headers = await login_staff(client, seed.teacher_phone)
    response = await client.post(
        "/admin/notifications/test",
        json={"parent_id": str(seed.parent_id), "template_scene": "lesson_remind"},
        headers=headers,
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "OUT_OF_SCOPE"


async def test_cross_tenant_parent_is_404(client, owner_engine, seed: Seed) -> None:
    """别的机构的家长 id → 404（跨租户读 404，不是 403）。"""

    headers = await login_staff(client, seed.admin_phone)
    response = await client.post(
        "/admin/notifications/test",
        json={"parent_id": str(seed.other_parent_id), "template_scene": "lesson_remind"},
        headers=headers,
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "PARENT_NOT_FOUND"


async def test_parent_cannot_send(client, owner_engine, seed: Seed) -> None:
    headers = await login_parent(client, unique_openid("adm-lonely"))
    response = await client.post(
        "/admin/notifications/test",
        json={"parent_id": str(seed.parent_id), "template_scene": "lesson_remind"},
        headers=headers,
    )
    assert response.status_code == 403, response.text
