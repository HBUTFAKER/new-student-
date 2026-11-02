"""平台后台（7.4）—— 租户列表 / 详情 / 套餐 / 越权 / 续费联动。

全部在真实 PostgreSQL 上跑（owner 连接跨租户）。What must never break：

* 列表：name 模糊搜索 + 分页、end_date 升序、sub_status 三态（expired/trial/active）；
* 详情：列表字段全量 + max_students/max_messages + admin_name/admin_phone，不存在 404；
* 越权：admin/registrar/teacher 调 `/platform/tenants`、详情、renew、usage 全 403；未登录 401；
* 续费联动：续费后详情 end_date 顺延、sub_status 恢复（与 7.2 口径一致）；
* platform_admin 登录：返回 role=platform_admin、tenant_id=null（前端据此落点 /platform/tenants）。
"""

import os
from datetime import date, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from app.services.ledger import business_today
from tests.auth_support import seed_staff, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run platform admin tests",
    ),
]


async def seed_tenant(
    owner_engine,
    *,
    name: str,
    plan_name: str,
    end_date: date,
    admin_phone: str | None = None,
) -> UUID:
    """建一个租户（可选建一个 admin 账号），返回 tenant_id。"""

    tenant_id, plan_id = uuid4(), uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
            {"id": tenant_id, "name": name},
        )
        await connection.execute(
            text(
                "INSERT INTO plan (id, name, max_students, max_messages, price) "
                "VALUES (:id, :name, 100, 1000, 0)"
            ),
            {"id": plan_id, "name": plan_name},
        )
        await connection.execute(
            text(
                "INSERT INTO tenant_subscription "
                "(id, tenant_id, plan_id, start_date, end_date, status) "
                "VALUES (:id, :t, :p, :start, :end, 'active')"
            ),
            {
                "id": uuid4(),
                "t": tenant_id,
                "p": plan_id,
                "start": date(2026, 1, 1),
                "end": end_date,
            },
        )
        if admin_phone:
            await connection.execute(
                text(
                    "INSERT INTO staff_account "
                    "(id, tenant_id, name, phone, password_hash, role, status) "
                    "VALUES (:id, :t, :name, :phone, '', 'admin', 'active')"
                ),
                {
                    "id": uuid4(),
                    "t": tenant_id,
                    "name": "机构管理员",
                    "phone": admin_phone,
                },
            )
    return tenant_id


async def platform_headers(client: httpx.AsyncClient, owner_engine) -> dict[str, str]:
    platform = await seed_staff(
        owner_engine,
        tenant_name=f"平台运营-{uuid4().hex[:8]}",
        role="platform_admin",
        phone=unique_phone(),
    )
    response = await client.post(
        "/auth/login", json={"phone": platform.phone, "password": platform.password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def login_headers(client: httpx.AsyncClient, phone: str, password: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"phone": phone, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# ---------------------------------------------------------------- 列表


async def test_list_tenants_fields_status_sort_search_page(client, owner_engine) -> None:
    prefix = f"列表机构-{uuid4().hex[:8]}-"
    today = business_today()

    expired = await seed_tenant(
        owner_engine, name=f"{prefix}过期", plan_name="基础版", end_date=today - timedelta(days=1)
    )
    trial = await seed_tenant(
        owner_engine, name=f"{prefix}试用", plan_name="试用版", end_date=today + timedelta(days=30)
    )
    active = await seed_tenant(
        owner_engine, name=f"{prefix}正常", plan_name="基础版", end_date=today + timedelta(days=60)
    )

    headers = await platform_headers(client, owner_engine)

    # name 模糊搜索命中这 3 个（平台运营宿主租户被前缀过滤掉）
    resp = await client.get("/platform/tenants", params={"name": prefix}, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3

    # end_date 升序：过期(-1) < 试用(+30) < 正常(+60)
    ordered = [item["tenant_id"] for item in body["items"]]
    assert ordered == [str(expired), str(trial), str(active)]

    status_by_id = {item["tenant_id"]: item["sub_status"] for item in body["items"]}
    assert status_by_id[str(expired)] == "expired"
    assert status_by_id[str(trial)] == "trial"
    assert status_by_id[str(active)] == "active"

    # 字段齐全（抽第一行核验）
    first = body["items"][0]
    for field in (
        "tenant_id",
        "name",
        "created_at",
        "plan_name",
        "plan_id",
        "start_date",
        "end_date",
        "days_left",
        "sub_status",
        "students_used",
        "messages_used",
        "period",
    ):
        assert field in first, f"缺字段 {field}"

    # name 精确模糊命中：搜「试用」只剩 1 条
    search = await client.get(
        "/platform/tenants", params={"name": f"{prefix}试用"}, headers=headers
    )
    assert search.status_code == 200
    assert search.json()["total"] == 1
    assert search.json()["items"][0]["tenant_id"] == str(trial)

    # 分页：page_size=1 → total 仍 3，items 只 1 条
    paged = await client.get(
        "/platform/tenants", params={"name": prefix, "page": 2, "page_size": 1}, headers=headers
    )
    assert paged.status_code == 200
    assert paged.json()["total"] == 3
    assert len(paged.json()["items"]) == 1
    assert paged.json()["items"][0]["tenant_id"] == str(trial)  # 第 2 页 = 试用


# ---------------------------------------------------------------- 详情


async def test_tenant_detail_full_fields_and_404(client, owner_engine) -> None:
    admin_phone = unique_phone()
    tenant_id = await seed_tenant(
        owner_engine,
        name=f"详情机构-{uuid4().hex[:8]}",
        plan_name="基础版",
        end_date=business_today() + timedelta(days=30),
        admin_phone=admin_phone,
    )
    headers = await platform_headers(client, owner_engine)

    resp = await client.get(f"/platform/tenants/{tenant_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tenant_id"] == str(tenant_id)
    assert body["plan_name"] == "基础版"
    assert body["sub_status"] == "active"
    assert body["max_students"] == 100
    assert body["max_messages"] == 1000
    assert body["admin_name"] == "机构管理员"
    assert body["admin_phone"] == admin_phone

    missing = await client.get(f"/platform/tenants/{uuid4()}", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "TENANT_NOT_FOUND"


# ---------------------------------------------------------------- 越权 + 未登录


async def test_platform_endpoints_forbidden_for_tenant_roles(client, owner_engine) -> None:
    tenant_id = await seed_tenant(
        owner_engine,
        name=f"越权机构-{uuid4().hex[:8]}",
        plan_name="基础版",
        end_date=business_today() + timedelta(days=30),
    )
    paths = [
        ("GET", "/platform/tenants", None),
        ("GET", f"/platform/tenants/{tenant_id}", None),
        ("GET", f"/platform/tenants/{tenant_id}/usage", None),
        ("GET", "/platform/plans", None),
        ("POST", f"/platform/tenants/{tenant_id}/renew", {"days": 30}),
    ]

    for role in ("admin", "registrar", "teacher"):
        staff = await seed_staff(
            owner_engine,
            tenant_name=f"越权{role}-{uuid4().hex[:8]}",
            role=role,
            phone=unique_phone(),
        )
        headers = await login_headers(client, staff.phone, staff.password)
        for method, path, json in paths:
            resp = await client.request(method, path, json=json, headers=headers)
            assert resp.status_code == 403, (
                f"{role} {method} {path} 应 403，实际 {resp.status_code}"
            )

    # 未登录 401
    unauth = await client.get("/platform/tenants")
    assert unauth.status_code == 401


# ---------------------------------------------------------------- 续费联动


async def test_renew_updates_detail_end_date_and_status(client, owner_engine) -> None:
    tenant_id = await seed_tenant(
        owner_engine,
        name=f"续费机构-{uuid4().hex[:8]}",
        plan_name="基础版",
        end_date=business_today() - timedelta(days=3),
    )
    headers = await platform_headers(client, owner_engine)

    before = await client.get(f"/platform/tenants/{tenant_id}", headers=headers)
    assert before.json()["sub_status"] == "expired"

    renew = await client.post(
        f"/platform/tenants/{tenant_id}/renew", json={"days": 30}, headers=headers
    )
    assert renew.status_code == 200, renew.text

    after = await client.get(f"/platform/tenants/{tenant_id}", headers=headers)
    assert after.status_code == 200
    # 已过期机构从今天起顺延（不白送过期天数）
    assert after.json()["end_date"] == (business_today() + timedelta(days=30)).isoformat()
    assert after.json()["sub_status"] == "active"
    assert after.json()["days_left"] == 30


# ---------------------------------------------------------------- 登录落点


async def test_platform_admin_login_returns_platform_role_and_null_tenant(
    client, owner_engine
) -> None:
    platform = await seed_staff(
        owner_engine,
        tenant_name=f"平台落点-{uuid4().hex[:8]}",
        role="platform_admin",
        phone=unique_phone(),
    )
    resp = await client.post(
        "/auth/login", json={"phone": platform.phone, "password": platform.password}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role"] == "platform_admin"
    assert body["tenant_id"] is None
