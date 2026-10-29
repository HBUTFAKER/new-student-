"""Staff login, refresh and WeChat login behaviour against real PostgreSQL."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.security import decode_token
from tests.auth_support import seed_family, seed_staff, unique_openid, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run auth tests",
    ),
]


async def test_staff_login_returns_signed_token(client, owner_engine) -> None:
    staff = await seed_staff(
        owner_engine, tenant_name="登录机构", role="registrar", phone=unique_phone()
    )

    response = await client.post(
        "/auth/login", json={"phone": staff.phone, "password": staff.password}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["role"] == "registrar"
    assert body["tenant_id"] == str(staff.tenant_id)
    claims = decode_token(body["access_token"], expected_type="access")
    assert claims.subject_id == staff.id
    assert claims.tenant_id == staff.tenant_id
    assert claims.role == "registrar"


async def test_login_failures_share_one_error_contract(client, owner_engine) -> None:
    staff = await seed_staff(
        owner_engine, tenant_name="错误密码机构", role="admin", phone=unique_phone()
    )
    disabled = await seed_staff(
        owner_engine,
        tenant_name="停用机构",
        role="admin",
        phone=unique_phone(),
        status="disabled",
    )

    wrong = await client.post(
        "/auth/login",
        json={"phone": staff.phone, "password": "wrong-password"},
        headers={"X-Request-ID": "trace-wrong-password"},
    )
    unknown = await client.post(
        "/auth/login", json={"phone": "13999999999", "password": "whatever"}
    )
    inactive = await client.post(
        "/auth/login", json={"phone": disabled.phone, "password": disabled.password}
    )

    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "AUTH_FAILED"
    assert wrong.json()["error"]["trace_id"] == "trace-wrong-password"
    assert wrong.headers["X-Request-ID"] == "trace-wrong-password"
    assert unknown.status_code == 401
    assert inactive.status_code == 401


async def test_same_phone_in_two_tenants_is_ambiguous(client, owner_engine) -> None:
    first = await seed_staff(owner_engine, tenant_name="机构甲", role="admin", phone=unique_phone())
    await seed_staff(
        owner_engine,
        tenant_name="机构乙",
        role="admin",
        phone=first.phone,
        password=first.password,
    )

    response = await client.post(
        "/auth/login", json={"phone": first.phone, "password": first.password}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_FAILED"


async def test_refresh_rotates_access_and_rejects_wrong_token_type(client, owner_engine) -> None:
    staff = await seed_staff(
        owner_engine, tenant_name="刷新机构", role="admin", phone=unique_phone()
    )
    login = await client.post(
        "/auth/login", json={"phone": staff.phone, "password": staff.password}
    )
    tokens = login.json()

    refreshed = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    claims = decode_token(refreshed.json()["access_token"], expected_type="access")
    assert claims.tenant_id == staff.tenant_id

    wrong_type = await client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert wrong_type.status_code == 401
    assert wrong_type.json()["error"]["code"] == "TOKEN_INVALID"


async def test_expired_refresh_token_reports_token_expired(client) -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    expired = jwt.encode(
        {
            "sub": str(uuid4()),
            "tenant_id": None,
            "role": "admin",
            "scope_type": "tenant",
            "type": "refresh",
            "iat": int((now - timedelta(hours=2)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    response = await client.post("/auth/refresh", json={"refresh_token": expired})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


async def test_wechat_login_creates_identity_and_reuses_it(client, owner_engine) -> None:
    code = unique_openid("new-parent")
    first = await client.post("/auth/wechat-login", json={"wx_code": code})
    second = await client.post("/auth/wechat-login", json={"wx_code": code})

    assert first.status_code == 200, first.text
    assert first.json()["role"] == "parent"
    assert first.json()["tenant_id"] is None
    assert first.json()["bound_students"] == 0
    assert (
        decode_token(first.json()["access_token"], expected_type="access").subject_id
        == decode_token(second.json()["access_token"], expected_type="access").subject_id
    )

    async with owner_engine.connect() as connection:
        openid_rows = await connection.scalar(
            text("SELECT count(*) FROM wechat_openid WHERE openid = :openid"),
            {"openid": code},
        )
    assert openid_rows == 1


async def test_unionid_merges_two_openids_into_one_wechat_user(client, owner_engine) -> None:
    openid_one, openid_two = unique_openid("mini"), unique_openid("other")
    unionid = unique_openid("union")
    first = await client.post("/auth/wechat-login", json={"wx_code": f"{openid_one}|{unionid}"})
    second = await client.post("/auth/wechat-login", json={"wx_code": f"{openid_two}|{unionid}"})

    first_sub = decode_token(first.json()["access_token"], expected_type="access").subject_id
    second_sub = decode_token(second.json()["access_token"], expected_type="access").subject_id
    assert first_sub == second_sub

    async with owner_engine.connect() as connection:
        count = await connection.scalar(
            text("SELECT count(*) FROM wechat_openid WHERE wechat_user_id = :id"),
            {"id": first_sub},
        )
    assert count == 2


async def test_conflicting_unionid_stops_auto_merge(client, owner_engine) -> None:
    openid = unique_openid("conflict")
    original_unionid = unique_openid("union-original")
    await seed_family(
        owner_engine,
        entries=[("冲突机构", "冲突学生")],
        openid=openid,
        unionid=original_unionid,
    )

    response = await client.post(
        "/auth/wechat-login", json={"wx_code": f"{openid}|{unique_openid('union-different')}"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "WX_IDENTITY_CONFLICT"


async def test_wechat_login_single_binding_activates_student(client, owner_engine) -> None:
    openid = unique_openid("single-parent")
    family = await seed_family(
        owner_engine,
        entries=[("单娃机构", "单娃学生")],
        openid=openid,
    )

    response = await client.post("/auth/wechat-login", json={"wx_code": openid})

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["bound_students"] == 1
    assert body["tenant_id"] == str(family.tenant_ids[0])
    assert body["active_student_id"] == str(family.student_ids[0])


async def test_multi_tenant_parent_must_choose_then_token_is_resigned(client, owner_engine) -> None:
    openid = unique_openid("multi-parent")
    family = await seed_family(
        owner_engine,
        entries=[("多娃机构甲", "甲学生"), ("多娃机构乙", "乙学生")],
        openid=openid,
    )
    login = await client.post("/auth/wechat-login", json={"wx_code": openid})
    body = login.json()
    assert body["bound_students"] == 2
    assert body["tenant_id"] is None

    students = await client.get(
        "/me/students", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert students.status_code == 200
    assert len(students.json()["items"]) == 2

    switched = await client.post(
        "/me/active-student",
        json={"student_id": str(family.student_ids[1])},
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert switched.status_code == 200
    assert switched.json()["tenant_id"] == str(family.tenant_ids[1])
    assert switched.json()["active_student_id"] == str(family.student_ids[1])
    claims = decode_token(switched.json()["access_token"], expected_type="access")
    assert claims.tenant_id == family.tenant_ids[1]

    outside = await client.post(
        "/me/active-student",
        json={"student_id": str(uuid4())},
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert outside.status_code == 403
    assert outside.json()["error"]["code"] == "OUT_OF_BINDING_SET"


async def test_switch_rejects_archived_student(client, owner_engine) -> None:
    openid = unique_openid("archived-parent")
    family = await seed_family(
        owner_engine,
        entries=[("归档机构", "归档学生")],
        openid=openid,
    )
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE student SET status = 'archived' WHERE id = :id"),
            {"id": family.student_ids[0]},
        )

    login = await client.post("/auth/wechat-login", json={"wx_code": openid})
    response = await client.post(
        "/me/active-student",
        json={"student_id": str(family.student_ids[0])},
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "OUT_OF_SCOPE"
