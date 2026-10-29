"""Tenant holidays: the date list lesson generation must skip (step 3.2)."""

import os
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from app.core.security import hash_password
from tests.auth_support import unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run holiday tests",
    ),
]

PASSWORD = "holiday-password"


@dataclass(frozen=True, slots=True)
class HolidaySeed:
    tenant_id: UUID
    admin_phone: str
    teacher_phone: str


async def login(client: httpx.AsyncClient, phone: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"phone": phone, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def holiday_seed(owner_engine) -> HolidaySeed:
    tenant_id, teacher_id = uuid4(), uuid4()
    admin_phone, teacher_phone = unique_phone(), unique_phone()
    password_hash = hash_password(PASSWORD)

    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
            {"id": tenant_id, "name": f"节假日机构-{uuid4().hex[:8]}"},
        )
        await connection.execute(
            text("INSERT INTO teacher (id, tenant_id, name) VALUES (:id, :tenant, '值班老师')"),
            {"id": teacher_id, "tenant": tenant_id},
        )
        for phone, role, linked in (
            (admin_phone, "admin", None),
            (teacher_phone, "teacher", teacher_id),
        ):
            await connection.execute(
                text(
                    "INSERT INTO staff_account "
                    "(id, tenant_id, name, phone, password_hash, role, teacher_id, status) "
                    "VALUES (:id, :tenant, :name, :phone, :hash, :role, :teacher, 'active')"
                ),
                {
                    "id": uuid4(),
                    "tenant": tenant_id,
                    "name": f"员工-{role}",
                    "phone": phone,
                    "hash": password_hash,
                    "role": role,
                    "teacher": linked,
                },
            )

    return HolidaySeed(tenant_id=tenant_id, admin_phone=admin_phone, teacher_phone=teacher_phone)


async def test_batch_register_is_idempotent_per_date(client, holiday_seed: HolidaySeed) -> None:
    headers = await login(client, holiday_seed.admin_phone)

    first = await client.post(
        "/tenant-holidays",
        json={"dates": ["2026-10-01", "2026-10-02", "2026-10-03"], "name": "国庆"},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["created"] == 3
    assert body["skipped"] == 0
    assert [item["date"] for item in body["items"]] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert body["items"][0]["name"] == "国庆"

    # 重复日期不再插入，只补新的那一天；同一次请求里的重复也只算一次。
    second = await client.post(
        "/tenant-holidays",
        json={"dates": ["2026-10-03", "2026-10-04", "2026-10-04"]},
        headers=headers,
    )
    assert second.status_code == 200, second.text
    assert second.json()["created"] == 1
    assert second.json()["skipped"] == 2
    assert [item["date"] for item in second.json()["items"]] == ["2026-10-03", "2026-10-04"]

    listing = await client.get("/tenant-holidays", params={"year": 2026}, headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 4
    assert (await client.get("/tenant-holidays", params={"year": 2027}, headers=headers)).json()[
        "total"
    ] == 0

    removed = await client.delete(f"/tenant-holidays/{body['items'][0]['id']}", headers=headers)
    assert removed.status_code == 204
    again = await client.delete(f"/tenant-holidays/{body['items'][0]['id']}", headers=headers)
    assert again.status_code == 404
    assert again.json()["error"]["code"] == "HOLIDAY_NOT_FOUND"
    assert (await client.get("/tenant-holidays", headers=headers)).json()["total"] == 3


async def test_holiday_validation_and_permissions(client, holiday_seed: HolidaySeed) -> None:
    headers = await login(client, holiday_seed.admin_phone)

    empty = await client.post("/tenant-holidays", json={"dates": []}, headers=headers)
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "VALIDATION_ERROR"

    too_many = await client.post(
        "/tenant-holidays",
        json={"dates": [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(61)]},
        headers=headers,
    )
    assert too_many.status_code == 422

    bad_year = await client.get("/tenant-holidays", params={"year": 1999}, headers=headers)
    assert bad_year.status_code == 422

    teacher = await login(client, holiday_seed.teacher_phone)
    for response in (
        await client.get("/tenant-holidays", headers=teacher),
        await client.post("/tenant-holidays", json={"dates": ["2026-12-31"]}, headers=teacher),
    ):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "OUT_OF_SCOPE"


async def test_cross_tenant_holidays_are_invisible(
    client, owner_engine, holiday_seed: HolidaySeed
) -> None:
    other_tenant, foreign_holiday = uuid4(), uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
            {"id": other_tenant, "name": f"别家机构-{uuid4().hex[:8]}"},
        )
        await connection.execute(
            text(
                "INSERT INTO tenant_holiday (id, tenant_id, date, name) "
                "VALUES (:id, :tenant, '2026-10-01', '别家国庆')"
            ),
            {"id": foreign_holiday, "tenant": other_tenant},
        )

    headers = await login(client, holiday_seed.admin_phone)
    listing = await client.get("/tenant-holidays", headers=headers)
    assert listing.status_code == 200
    assert str(foreign_holiday) not in {item["id"] for item in listing.json()["items"]}

    removed = await client.delete(f"/tenant-holidays/{foreign_holiday}", headers=headers)
    assert removed.status_code == 404
    assert removed.json()["error"]["code"] == "HOLIDAY_NOT_FOUND"

    # 别家的日期不能挡住自家课次（下面这条只是登记自己的同一天，互不影响）。
    own = await client.post("/tenant-holidays", json={"dates": ["2026-10-01"]}, headers=headers)
    assert own.status_code == 200
    assert own.json()["created"] == 1
