"""管理端通知统计 / 补发接口（6.4 / GET /admin/notifications/stats + POST .../retry）。

覆盖：stats 数字与库内状态一致、空数据 success_rate=null、teacher 403、跨租户 404；
retry exhausted 重置回 pending、succeeded 409、pending 409、跨租户 404。
"""

import os
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from app.core.security import hash_password
from tests.auth_support import seed_family, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run admin notification stats tests",
    ),
]

PASSWORD = "admin-stats-password"


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


async def insert_notification(
    engine, *, tenant_id: UUID, dedupe_key: str, status: str, attempts: int = 0
) -> UUID:
    nid = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO notification (id, tenant_id, event_type, dedupe_key, status, attempts)"
                " VALUES (:id, :t, 'test_event', :k, :status, :attempts)"
            ),
            {"id": nid, "t": tenant_id, "k": dedupe_key, "status": status, "attempts": attempts},
        )
    return nid


async def backdate(engine, nid: UUID, days: int) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE notification SET created_at = now() - :d * interval '1 day' WHERE id = :id"
            ),
            {"d": days, "id": nid},
        )


async def fetch_notification(engine, nid: UUID) -> dict:
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text("SELECT status, attempts, tenant_id FROM notification WHERE id = :id"),
                    {"id": nid},
                )
            )
            .mappings()
            .one()
        )
    return dict(row)


@pytest.fixture(autouse=True)
async def clean_slate(owner_engine):
    async with owner_engine.begin() as connection:
        await connection.execute(text("DELETE FROM notification"))
    yield


@pytest.fixture
async def seed(owner_engine) -> Seed:
    family = await seed_family(owner_engine, entries=[("统计机构A", "小明")])
    other_family = await seed_family(owner_engine, entries=[("统计机构B", "小红")])
    tenant_id = family.tenant_ids[0]
    return Seed(
        tenant_id=tenant_id,
        other_tenant_id=other_family.tenant_ids[0],
        admin_phone=await add_staff(owner_engine, tenant_id=tenant_id, role="admin"),
        teacher_phone=await add_staff(owner_engine, tenant_id=tenant_id, role="teacher"),
    )


async def test_stats_match_db_state(client, owner_engine, seed: Seed) -> None:
    headers = await login_staff(client, seed.admin_phone)
    await insert_notification(
        owner_engine, tenant_id=seed.tenant_id, dedupe_key="s-1", status="succeeded"
    )
    await insert_notification(
        owner_engine, tenant_id=seed.tenant_id, dedupe_key="s-2", status="succeeded"
    )
    await insert_notification(
        owner_engine, tenant_id=seed.tenant_id, dedupe_key="e-1", status="exhausted"
    )
    await insert_notification(
        owner_engine, tenant_id=seed.tenant_id, dedupe_key="p-1", status="pending"
    )

    # 一条 8 天前的，不该进「近 7 天」
    old = await insert_notification(
        owner_engine, tenant_id=seed.tenant_id, dedupe_key="old-1", status="succeeded"
    )
    await backdate(owner_engine, old, 8)

    response = await client.get("/admin/notifications/stats", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["today"]["total"] == 4
    assert data["today"]["succeeded"] == 2
    assert data["today"]["exhausted"] == 1
    assert data["today"]["pending"] == 1
    assert data["today"]["success_rate"] == 0.5

    assert data["last_7_days"]["total"] == 4  # 8 天前的不算
    assert data["last_7_days"]["success_rate"] == 0.5


async def test_stats_empty_success_rate_is_null(client, owner_engine, seed: Seed) -> None:
    headers = await login_staff(client, seed.admin_phone)
    response = await client.get("/admin/notifications/stats", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["today"]["total"] == 0
    assert data["today"]["success_rate"] is None
    assert data["last_7_days"]["success_rate"] is None


async def test_stats_teacher_forbidden(client, owner_engine, seed: Seed) -> None:
    headers = await login_staff(client, seed.teacher_phone)
    response = await client.get("/admin/notifications/stats", headers=headers)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "OUT_OF_SCOPE"


async def test_retry_exhausted_resets_to_pending(client, owner_engine, seed: Seed) -> None:
    headers = await login_staff(client, seed.admin_phone)
    nid = await insert_notification(
        owner_engine, tenant_id=seed.tenant_id, dedupe_key="r-1", status="exhausted", attempts=3
    )
    response = await client.post(f"/admin/notifications/{nid}/retry", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"

    row = await fetch_notification(owner_engine, nid)
    assert row["status"] == "pending"
    assert row["attempts"] == 0


async def test_retry_succeeded_is_409(client, owner_engine, seed: Seed) -> None:
    headers = await login_staff(client, seed.admin_phone)
    nid = await insert_notification(
        owner_engine, tenant_id=seed.tenant_id, dedupe_key="r-2", status="succeeded"
    )
    response = await client.post(f"/admin/notifications/{nid}/retry", headers=headers)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "NOTIFICATION_ALREADY_SUCCEEDED"


async def test_retry_pending_is_409(client, owner_engine, seed: Seed) -> None:
    headers = await login_staff(client, seed.admin_phone)
    nid = await insert_notification(
        owner_engine, tenant_id=seed.tenant_id, dedupe_key="r-3", status="pending"
    )
    response = await client.post(f"/admin/notifications/{nid}/retry", headers=headers)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "NOTIFICATION_NOT_RETRYABLE"


async def test_retry_cross_tenant_is_404(client, owner_engine, seed: Seed) -> None:
    headers = await login_staff(client, seed.admin_phone)
    other_nid = await insert_notification(
        owner_engine, tenant_id=seed.other_tenant_id, dedupe_key="r-4", status="exhausted"
    )
    response = await client.post(f"/admin/notifications/{other_nid}/retry", headers=headers)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOTIFICATION_NOT_FOUND"
