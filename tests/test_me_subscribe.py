"""家长端订阅授权收集（step 5.4 / 033 指令）。

覆盖：模板只来自后端配置、teacher/admin 403、未选孩子 403 且零落库、
accept 累加（两次=2）、reject 不清零、ban 落状态、未知模板整批 422 不落库、
``wechat_user_id`` 只认 token（body 里塞别人的 id 无效）、跨机构隔离。

订阅授权是**机构级**的（挂 tenant + wechat_user + template），跟具体孩子无关，
所以这里的闸门是 ``parent_tenant_session``，不是 5.3 的 ``child_session``。
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
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run subscribe tests",
    ),
]

PASSWORD = "subscribe-password"
REMIND = "stub_lesson_remind"
BALANCE = "stub_balance_warn"
CANCELLED = "stub_lesson_cancelled"


@dataclass(frozen=True, slots=True)
class Seed:
    tenant_id: UUID
    student_id: UUID
    other_tenant_id: UUID
    other_student_id: UUID
    openid: str
    wechat_user_id: UUID
    other_openid: str
    other_wechat_user_id: UUID
    admin_phone: str
    teacher_phone: str


async def login_parent(client: httpx.AsyncClient, openid: str) -> dict[str, str]:
    response = await client.post("/auth/wechat-login", json={"wx_code": openid})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def login_staff(client: httpx.AsyncClient, phone: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"phone": phone, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def add_staff(engine, tenant_id: UUID, role: str) -> str:
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


async def count_rows(engine, *, tenant_id: UUID, wechat_user_id: UUID | None = None) -> int:
    sql = "SELECT count(*) FROM subscribe_auth WHERE tenant_id = :t"
    params: dict = {"t": tenant_id}
    if wechat_user_id is not None:
        sql += " AND wechat_user_id = :u"
        params["u"] = wechat_user_id
    async with engine.connect() as connection:
        return int(await connection.scalar(text(sql), params) or 0)


async def fetch_row(engine, *, tenant_id: UUID, wechat_user_id: UUID, template_id: str) -> dict:
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        "SELECT status, available_count, updated_at FROM subscribe_auth "
                        "WHERE tenant_id = :t AND wechat_user_id = :u AND template_id = :tpl"
                    ),
                    {"t": tenant_id, "u": wechat_user_id, "tpl": template_id},
                )
            )
            .mappings()
            .one()
        )
    return dict(row)


@pytest.fixture
async def seed(owner_engine) -> Seed:
    """一个机构 + 一个家长（已选孩子），外加另一个机构当越权靶子。"""

    openid = unique_openid("sub-parent")
    family = await seed_family(owner_engine, entries=[("订阅机构", "小明")], openid=openid)
    other_openid = unique_openid("sub-other")
    other_family = await seed_family(
        owner_engine, entries=[("别家机构", "小红")], openid=other_openid
    )

    tenant_id = family.tenant_ids[0]
    other_tenant_id = other_family.tenant_ids[0]

    return Seed(
        tenant_id=tenant_id,
        student_id=family.student_ids[0],
        other_tenant_id=other_tenant_id,
        other_student_id=other_family.student_ids[0],
        openid=openid,
        wechat_user_id=family.wechat_user_id,
        other_openid=other_openid,
        other_wechat_user_id=other_family.wechat_user_id,
        admin_phone=await add_staff(owner_engine, tenant_id, "admin"),
        teacher_phone=await add_staff(owner_engine, tenant_id, "teacher"),
    )


async def test_templates_come_from_backend_config(client, seed: Seed) -> None:
    """GET 只回配置里的模板，字段齐全（小程序靠 type 区分一次性/长期）。"""

    headers = await login_parent(client, seed.openid)
    response = await client.get("/me/subscribe-templates", headers=headers)

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["template_id"] for item in items] == [REMIND, BALANCE, CANCELLED]
    assert all(item["type"] in {"one_time", "long_term"} for item in items)
    assert all(item["scene"] for item in items)


async def test_teacher_and_admin_cannot_read_templates(client, seed: Seed) -> None:
    for phone in (seed.teacher_phone, seed.admin_phone):
        headers = await login_staff(client, phone)
        response = await client.get("/me/subscribe-templates", headers=headers)
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "OUT_OF_SCOPE"


async def test_accept_twice_accumulates_quota(client, owner_engine, seed: Seed) -> None:
    """微信一次性订阅的语义：每接受一次 +1，不是「已授权就覆盖」。"""

    headers = await login_parent(client, seed.openid)
    payload = {"results": [{"template_id": REMIND, "status": "accept"}]}

    first = await client.post("/me/subscribe-auths", json=payload, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["items"][0]["available_count"] == 1

    second = await client.post("/me/subscribe-auths", json=payload, headers=headers)
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["items"][0]["available_count"] == 2
    assert body["items"][0]["status"] == "authorized"
    assert body["total"] == 1

    row = await fetch_row(
        owner_engine,
        tenant_id=seed.tenant_id,
        wechat_user_id=seed.wechat_user_id,
        template_id=REMIND,
    )
    assert row["available_count"] == 2
    assert row["updated_at"] is not None
    # 唯一键保证一条记录被反复更新，而不是每次插一行
    assert await count_rows(owner_engine, tenant_id=seed.tenant_id) == 1


async def test_reject_keeps_existing_quota(client, owner_engine, seed: Seed) -> None:
    headers = await login_parent(client, seed.openid)
    accept = {"results": [{"template_id": REMIND, "status": "accept"}]}
    reject = {"results": [{"template_id": REMIND, "status": "reject"}]}

    await client.post("/me/subscribe-auths", json=accept, headers=headers)
    response = await client.post("/me/subscribe-auths", json=reject, headers=headers)

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["status"] == "rejected"
    assert item["available_count"] == 1  # 之前攒下的额度不被清零

    row = await fetch_row(
        owner_engine,
        tenant_id=seed.tenant_id,
        wechat_user_id=seed.wechat_user_id,
        template_id=REMIND,
    )
    assert row["status"] == "rejected"
    assert row["available_count"] == 1


async def test_ban_is_recorded_without_quota(client, owner_engine, seed: Seed) -> None:
    """ban =「总是保持以上选择」，落 banned；从没接受过就没有额度。"""

    headers = await login_parent(client, seed.openid)
    response = await client.post(
        "/me/subscribe-auths",
        json={"results": [{"template_id": BALANCE, "status": "ban"}]},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["items"][0] == {
        "template_id": BALANCE,
        "status": "banned",
        "available_count": 0,
    }
    row = await fetch_row(
        owner_engine,
        tenant_id=seed.tenant_id,
        wechat_user_id=seed.wechat_user_id,
        template_id=BALANCE,
    )
    assert row["status"] == "banned"


async def test_unknown_template_rejects_whole_batch(client, owner_engine, seed: Seed) -> None:
    """一个模板不在配置里 → 整批 422，连合法的那个也不许落库。"""

    headers = await login_parent(client, seed.openid)
    response = await client.post(
        "/me/subscribe-auths",
        json={
            "results": [
                {"template_id": REMIND, "status": "accept"},
                {"template_id": "made-up-template", "status": "accept"},
            ]
        },
        headers=headers,
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "SUBSCRIBE_TEMPLATE_UNKNOWN"
    assert await count_rows(owner_engine, tenant_id=seed.tenant_id) == 0


async def test_parent_without_binding_gets_403_and_writes_nothing(client, owner_engine) -> None:
    """没绑定任何孩子的微信身份：token 里没有 tenant → 403，一行都不许落。"""

    openid = unique_openid("sub-lonely")
    headers = await login_parent(client, openid)
    response = await client.post(
        "/me/subscribe-auths",
        json={"results": [{"template_id": REMIND, "status": "accept"}]},
        headers=headers,
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "OUT_OF_SCOPE"

    async with owner_engine.connect() as connection:
        rows = int(
            await connection.scalar(
                text(
                    "SELECT count(*) FROM subscribe_auth a "
                    "JOIN wechat_openid o ON o.wechat_user_id = a.wechat_user_id "
                    "WHERE o.openid = :openid"
                ),
                {"openid": openid},
            )
            or 0
        )
    assert rows == 0


async def test_wechat_user_id_comes_from_token(client, owner_engine, seed: Seed) -> None:
    """body 里塞别人的 wechat_user_id / tenant_id 一律无效，落库只认 token。"""

    headers = await login_parent(client, seed.openid)
    response = await client.post(
        "/me/subscribe-auths",
        json={
            "results": [{"template_id": REMIND, "status": "accept"}],
            "wechat_user_id": str(seed.other_wechat_user_id),
            "tenant_id": str(seed.other_tenant_id),
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert await count_rows(owner_engine, tenant_id=seed.tenant_id) == 1
    assert (
        await count_rows(
            owner_engine, tenant_id=seed.tenant_id, wechat_user_id=seed.other_wechat_user_id
        )
        == 0
    )
    assert await count_rows(owner_engine, tenant_id=seed.other_tenant_id) == 0


async def test_teacher_cannot_post(client, owner_engine, seed: Seed) -> None:
    headers = await login_staff(client, seed.teacher_phone)
    response = await client.post(
        "/me/subscribe-auths",
        json={"results": [{"template_id": REMIND, "status": "accept"}]},
        headers=headers,
    )

    assert response.status_code == 403, response.text
    assert await count_rows(owner_engine, tenant_id=seed.tenant_id) == 0


async def test_empty_results_is_422(client, seed: Seed) -> None:
    headers = await login_parent(client, seed.openid)
    response = await client.post("/me/subscribe-auths", json={"results": []}, headers=headers)
    assert response.status_code == 422, response.text


async def test_multi_tenant_parent_cannot_write_without_active_student(
    client, owner_engine
) -> None:
    """跨两个机构的家长：登录后未选孩子（token 无 tenant）→ 403；选了才写对应机构。"""

    openid = unique_openid("sub-multi")
    family = await seed_family(
        owner_engine, entries=[("多机构A", "小明"), ("多机构B", "小刚")], openid=openid
    )
    headers = await login_parent(client, openid)
    payload = {"results": [{"template_id": REMIND, "status": "accept"}]}

    blocked = await client.post("/me/subscribe-auths", json=payload, headers=headers)
    assert blocked.status_code == 403, blocked.text
    assert await count_rows(owner_engine, tenant_id=family.tenant_ids[0]) == 0
    assert await count_rows(owner_engine, tenant_id=family.tenant_ids[1]) == 0

    switched = await client.post(
        "/me/active-student",
        json={"student_id": str(family.student_ids[0])},
        headers=headers,
    )
    assert switched.status_code == 200, switched.text
    token = switched.json()["access_token"]

    accepted = await client.post(
        "/me/subscribe-auths",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert accepted.status_code == 200, accepted.text
    assert await count_rows(owner_engine, tenant_id=family.tenant_ids[0]) == 1
    assert await count_rows(owner_engine, tenant_id=family.tenant_ids[1]) == 0


async def test_requires_login(client) -> None:
    response = await client.get("/me/subscribe-templates")
    assert response.status_code == 401
