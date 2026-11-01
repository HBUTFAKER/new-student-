"""Staff account administration (US-301): create/revive, guarded update, self-deactivate."""

import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from app.core.security import hash_password
from tests.auth_support import seed_staff, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run staff admin tests",
    ),
]


async def seed_teacher_archive(owner_engine, tenant_id, name: str = "任教老师"):
    teacher_id = uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO teacher (id, tenant_id, name) VALUES (:id, :tenant, :name)"),
            {"id": teacher_id, "tenant": tenant_id, "name": name},
        )
    return teacher_id


async def seed_staff_in_tenant(owner_engine, tenant_id, *, role, phone, password, teacher_id=None):
    staff_id = uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO staff_account "
                "(id, tenant_id, name, phone, password_hash, role, teacher_id, status) "
                "VALUES (:id, :tenant, :name, :phone, :hash, :role, :teacher, 'active')"
            ),
            {
                "id": staff_id,
                "tenant": tenant_id,
                "name": f"{role}-{phone}",
                "phone": phone,
                "hash": hash_password(password),
                "role": role,
                "teacher": teacher_id,
            },
        )
    return {
        "id": staff_id,
        "tenant_id": tenant_id,
        "role": role,
        "phone": phone,
        "password": password,
    }


async def login_headers(client: httpx.AsyncClient, phone: str, password: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"phone": phone, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# ---------------------------------------------------------------- 全生命周期


async def test_create_disable_revive_lifecycle(client, owner_engine) -> None:
    admin = await seed_staff(
        owner_engine,
        tenant_name=f"员工机构-{uuid4().hex[:8]}",
        role="admin",
        phone=unique_phone(),
    )
    headers = await login_headers(client, admin.phone, admin.password)
    teacher_id = await seed_teacher_archive(owner_engine, admin.tenant_id)
    phone = unique_phone()

    created = await client.post(
        "/staff",
        json={
            "name": "张老师",
            "phone": phone,
            "role": "teacher",
            "password": "teacher-pass-123",
            "teacher_id": str(teacher_id),
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["revived"] is False
    staff_id = created.json()["id"]

    first_login = await client.post(
        "/auth/login", json={"phone": phone, "password": "teacher-pass-123"}
    )
    assert first_login.status_code == 200
    assert first_login.json()["role"] == "teacher"
    old_token = first_login.json()["access_token"]

    disabled = await client.patch(
        f"/staff/{staff_id}", json={"status": "inactive"}, headers=headers
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "inactive"

    relogin = await client.post(
        "/auth/login", json={"phone": phone, "password": "teacher-pass-123"}
    )
    assert relogin.status_code == 401
    assert relogin.json()["error"]["code"] == "AUTH_FAILED"
    assert "停用" in relogin.json()["error"]["message"]

    # 停用后旧 access token 调业务接口被拒（resolve_principal 的 status 校验）
    stale = await client.get("/students", headers={"Authorization": f"Bearer {old_token}"})
    assert stale.status_code == 401

    revived = await client.post(
        "/staff",
        json={
            "name": "新名字",
            "phone": phone,
            "role": "registrar",
            "password": "new-pass-12345",
        },
        headers=headers,
    )
    assert revived.status_code == 200
    assert revived.json()["revived"] is True
    assert revived.json()["id"] == staff_id
    assert revived.json()["name"] == "新名字"
    assert revived.json()["role"] == "registrar"

    final_login = await client.post(
        "/auth/login", json={"phone": phone, "password": "new-pass-12345"}
    )
    assert final_login.status_code == 200
    assert final_login.json()["role"] == "registrar"


async def test_active_phone_is_409(client, owner_engine) -> None:
    admin = await seed_staff(
        owner_engine,
        tenant_name=f"冲突机构-{uuid4().hex[:8]}",
        role="admin",
        phone=unique_phone(),
    )
    headers = await login_headers(client, admin.phone, admin.password)

    body = {
        "name": "教务甲",
        "phone": unique_phone(),
        "role": "registrar",
        "password": "registrar-pass-123",
    }
    first = await client.post("/staff", json=body, headers=headers)
    assert first.status_code == 201
    second = await client.post("/staff", json=body, headers=headers)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "PHONE_IN_USE"


# ---------------------------------------------------------------- 复活保留历史


async def test_revive_preserves_teacher_archive_history(client, owner_engine) -> None:
    admin = await seed_staff(
        owner_engine,
        tenant_name=f"历史机构-{uuid4().hex[:8]}",
        role="admin",
        phone=unique_phone(),
    )
    headers = await login_headers(client, admin.phone, admin.password)
    old_teacher = await seed_teacher_archive(owner_engine, admin.tenant_id, "旧老师")
    new_teacher = await seed_teacher_archive(owner_engine, admin.tenant_id, "新老师")

    course_id, classroom_id, class_id = uuid4(), uuid4(), uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO course (id, tenant_id, name) VALUES (:id, :tenant, '物理')"),
            {"id": course_id, "tenant": admin.tenant_id},
        )
        await connection.execute(
            text(
                "INSERT INTO classroom (id, tenant_id, name, capacity) "
                "VALUES (:id, :tenant, '教室A', 30)"
            ),
            {"id": classroom_id, "tenant": admin.tenant_id},
        )
        await connection.execute(
            text(
                "INSERT INTO class "
                "(id, tenant_id, course_id, teacher_id, classroom_id, name, capacity) "
                "VALUES (:id, :tenant, :course, :teacher, :room, '班级A', 20)"
            ),
            {
                "id": class_id,
                "tenant": admin.tenant_id,
                "course": course_id,
                "teacher": old_teacher,
                "room": classroom_id,
            },
        )

    phone = unique_phone()
    created = await client.post(
        "/staff",
        json={
            "name": "旧老师账号",
            "phone": phone,
            "role": "teacher",
            "password": "old-pass-12345",
            "teacher_id": str(old_teacher),
        },
        headers=headers,
    )
    assert created.status_code == 201
    staff_id = created.json()["id"]

    await client.patch(f"/staff/{staff_id}", json={"status": "inactive"}, headers=headers)

    revived = await client.post(
        "/staff",
        json={
            "name": "新老师账号",
            "phone": phone,
            "role": "teacher",
            "password": "new-pass-12345",
            "teacher_id": str(new_teacher),
        },
        headers=headers,
    )
    assert revived.status_code == 200
    assert revived.json()["teacher_id"] == str(new_teacher)

    async with owner_engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT teacher_id FROM class WHERE id = :id"), {"id": class_id}
            )
        ).one()
    assert row.teacher_id == old_teacher


# ---------------------------------------------------------------- 防呆


async def test_cannot_disable_self(client, owner_engine) -> None:
    admin = await seed_staff(
        owner_engine,
        tenant_name=f"防呆机构-{uuid4().hex[:8]}",
        role="admin",
        phone=unique_phone(),
    )
    await seed_staff_in_tenant(
        owner_engine, admin.tenant_id, role="admin", phone=unique_phone(), password="b-pass-12345"
    )
    headers = await login_headers(client, admin.phone, admin.password)

    response = await client.patch(
        f"/staff/{admin.id}", json={"status": "inactive"}, headers=headers
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CANNOT_DISABLE_SELF"


async def test_disable_last_admin_is_403(client, owner_engine) -> None:
    admin = await seed_staff(
        owner_engine,
        tenant_name=f"最后admin-{uuid4().hex[:8]}",
        role="admin",
        phone=unique_phone(),
    )
    other = await seed_staff_in_tenant(
        owner_engine, admin.tenant_id, role="admin", phone=unique_phone(), password="b-pass-12345"
    )
    headers = await login_headers(client, admin.phone, admin.password)

    first = await client.patch(
        f"/staff/{other['id']}", json={"status": "inactive"}, headers=headers
    )
    assert first.status_code == 200

    second = await client.patch(f"/staff/{admin.id}", json={"status": "inactive"}, headers=headers)
    assert second.status_code == 403
    assert second.json()["error"]["code"] == "LAST_ADMIN"


async def test_demote_only_admin_is_403(client, owner_engine) -> None:
    admin = await seed_staff(
        owner_engine,
        tenant_name=f"降级机构-{uuid4().hex[:8]}",
        role="admin",
        phone=unique_phone(),
    )
    headers = await login_headers(client, admin.phone, admin.password)

    response = await client.patch(f"/staff/{admin.id}", json={"role": "teacher"}, headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "LAST_ADMIN"


# ---------------------------------------------------------------- 越权


async def test_registrar_and_teacher_cannot_manage_staff(client, owner_engine) -> None:
    registrar = await seed_staff(
        owner_engine,
        tenant_name=f"越权机构-{uuid4().hex[:8]}",
        role="registrar",
        phone=unique_phone(),
    )
    headers = await login_headers(client, registrar.phone, registrar.password)

    post = await client.post(
        "/staff",
        json={"name": "x", "phone": unique_phone(), "role": "registrar", "password": "pass-12345"},
        headers=headers,
    )
    assert post.status_code == 403

    get = await client.get("/staff", headers=headers)
    assert get.status_code == 403

    patch = await client.patch(f"/staff/{uuid4()}", json={"name": "x"}, headers=headers)
    assert patch.status_code == 403


async def test_cross_tenant_staff_is_404(client, owner_engine) -> None:
    admin_a = await seed_staff(
        owner_engine,
        tenant_name=f"A机构-{uuid4().hex[:8]}",
        role="admin",
        phone=unique_phone(),
    )
    admin_b = await seed_staff(
        owner_engine,
        tenant_name=f"B机构-{uuid4().hex[:8]}",
        role="admin",
        phone=unique_phone(),
    )
    headers_a = await login_headers(client, admin_a.phone, admin_a.password)

    response = await client.patch(
        f"/staff/{admin_b.id}", json={"name": "越权改名"}, headers=headers_a
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "STAFF_NOT_FOUND"


# ---------------------------------------------------------------- 自助注销


async def test_self_deactivate_full_flow(client, owner_engine) -> None:
    teacher = await seed_staff(
        owner_engine,
        tenant_name=f"注销机构-{uuid4().hex[:8]}",
        role="teacher",
        phone=unique_phone(),
    )
    headers = await login_headers(client, teacher.phone, teacher.password)

    wrong = await client.request(
        "DELETE", "/me/account", json={"password": "wrong-password"}, headers=headers
    )
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "AUTH_FAILED"

    response = await client.request(
        "DELETE", "/me/account", json={"password": teacher.password}, headers=headers
    )
    assert response.status_code == 204

    relogin = await client.post(
        "/auth/login", json={"phone": teacher.phone, "password": teacher.password}
    )
    assert relogin.status_code == 401

    stale = await client.get("/students", headers=headers)
    assert stale.status_code == 401


async def test_only_admin_cannot_self_deactivate(client, owner_engine) -> None:
    admin = await seed_staff(
        owner_engine,
        tenant_name=f"唯一admin-{uuid4().hex[:8]}",
        role="admin",
        phone=unique_phone(),
    )
    headers = await login_headers(client, admin.phone, admin.password)

    response = await client.request(
        "DELETE", "/me/account", json={"password": admin.password}, headers=headers
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "LAST_ADMIN"


async def test_deactivated_account_can_be_revived_by_admin(client, owner_engine) -> None:
    admin = await seed_staff(
        owner_engine,
        tenant_name=f"复活机构-{uuid4().hex[:8]}",
        role="admin",
        phone=unique_phone(),
    )
    teacher_id = await seed_teacher_archive(owner_engine, admin.tenant_id)
    teacher = await seed_staff_in_tenant(
        owner_engine,
        admin.tenant_id,
        role="teacher",
        phone=unique_phone(),
        password="teacher-pass-123",
        teacher_id=teacher_id,
    )
    teacher_headers = await login_headers(client, teacher["phone"], "teacher-pass-123")
    await client.request(
        "DELETE", "/me/account", json={"password": "teacher-pass-123"}, headers=teacher_headers
    )

    admin_headers = await login_headers(client, admin.phone, admin.password)
    revived = await client.post(
        "/staff",
        json={
            "name": "复活老师",
            "phone": teacher["phone"],
            "role": "teacher",
            "password": "revived-pass-123",
            "teacher_id": str(teacher_id),
        },
        headers=admin_headers,
    )
    assert revived.status_code == 200
    assert revived.json()["revived"] is True
    assert revived.json()["status"] == "active"
