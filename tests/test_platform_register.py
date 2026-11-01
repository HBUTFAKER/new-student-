"""Public self-serve registration (7.1): trial plan + 14-day window, manual path unchanged."""

import os
from datetime import date, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from tests.auth_support import seed_staff, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run registration tests",
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


def register_payload(name: str | None = None, phone: str | None = None) -> dict[str, str]:
    return {
        "org_name": name or f"注册机构-{uuid4().hex[:8]}",
        "admin_name": "注册管理员",
        "admin_phone": phone or unique_phone(),
        "admin_password": "register-pass-123",
    }


async def test_register_creates_trial_tenant_and_admin_can_login(client, owner_engine) -> None:
    body = register_payload()

    # 公开：不带任何认证头
    response = await client.post("/platform/register", json=body)

    assert response.status_code == 201, response.text
    result = response.json()
    tenant_id = UUID(result["tenant_id"])
    assert result["name"] == body["org_name"]
    assert result["admin_phone"] == body["admin_phone"]
    assert result["trial_end_date"] == (date.today() + timedelta(days=14)).isoformat()

    async with owner_engine.connect() as connection:
        subscription = (
            await connection.execute(
                text(
                    "SELECT p.name AS plan_name, s.plan_id, s.start_date, s.end_date, s.status "
                    "FROM tenant_subscription s JOIN plan p ON p.id = s.plan_id "
                    "WHERE s.tenant_id = :id"
                ),
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
                text("SELECT role, status FROM staff_account WHERE tenant_id = :id"),
                {"id": tenant_id},
            )
        ).one()

    assert subscription.plan_name == "试用版"
    assert str(subscription.plan_id) == result["plan_id"]
    assert subscription.start_date == date.today()
    assert subscription.end_date == date.today() + timedelta(days=14)
    assert subscription.status == "active"
    assert org_config.absence_deduct_rule == "none"
    assert admin.role == "admin"
    assert admin.status == "active"

    login = await client.post(
        "/auth/login", json={"phone": body["admin_phone"], "password": body["admin_password"]}
    )
    assert login.status_code == 200, login.text
    assert login.json()["role"] == "admin"
    assert login.json()["tenant_id"] == str(tenant_id)


async def test_register_duplicate_name_is_409(client, owner_engine) -> None:
    body = register_payload()

    first = await client.post("/platform/register", json=body)
    second = await client.post("/platform/register", json=body)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "TENANT_NAME_EXISTS"


async def test_register_same_phone_across_tenants(client, owner_engine) -> None:
    phone = unique_phone()

    first = await client.post("/platform/register", json=register_payload(phone=phone))
    second = await client.post("/platform/register", json=register_payload(phone=phone))

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text


async def test_manual_provision_unchanged_base_plan_365_days(client, owner_engine) -> None:
    token = await platform_token(client, owner_engine)
    body = {
        "name": f"手动机构-{uuid4().hex[:8]}",
        "admin_name": "手动管理员",
        "admin_phone": unique_phone(),
        "admin_password": "manual-pass-123",
    }

    response = await client.post(
        "/platform/tenants", json=body, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 201, response.text
    tenant_id = UUID(response.json()["tenant_id"])
    async with owner_engine.connect() as connection:
        subscription = (
            await connection.execute(
                text(
                    "SELECT p.name AS plan_name, s.end_date "
                    "FROM tenant_subscription s JOIN plan p ON p.id = s.plan_id "
                    "WHERE s.tenant_id = :id"
                ),
                {"id": tenant_id},
            )
        ).one()

    assert subscription.plan_name == "基础版"
    assert subscription.end_date == date.today() + timedelta(days=365)


async def test_register_response_leaks_no_token_or_tenant_data(client, owner_engine) -> None:
    response = await client.post("/platform/register", json=register_payload())

    assert response.status_code == 201, response.text
    result = response.json()
    assert set(result.keys()) == {"tenant_id", "name", "admin_phone", "plan_id", "trial_end_date"}
    assert "access_token" not in result
    assert "token" not in result


async def test_register_validation_422(client, owner_engine) -> None:
    # 缺字段
    missing = await client.post("/platform/register", json={"org_name": "只有名字"})
    assert missing.status_code == 422

    # 密码弱（< 8 位）
    weak = register_payload()
    weak["admin_password"] = "short"
    assert (await client.post("/platform/register", json=weak)).status_code == 422

    # 空机构名
    empty = register_payload()
    empty["org_name"] = ""
    assert (await client.post("/platform/register", json=empty)).status_code == 422
