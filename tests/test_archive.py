"""Archive CRUD, teacher scope and invite-code binding (step 1.6)."""

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.core.security import hash_password
from tests.auth_support import unique_openid, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run archive tests",
    ),
]


@dataclass(frozen=True)
class ArchiveSeed:
    tenant_id: UUID
    admin_phone: str
    registrar_phone: str
    teacher_phone: str
    teacher_id: UUID
    other_teacher_id: UUID
    class_id: UUID
    other_class_id: UUID
    classroom_id: UUID
    other_classroom_id: UUID
    student_one: UUID
    student_two: UUID
    student_three: UUID


@pytest.fixture
async def archive_seed(owner_engine) -> ArchiveSeed:
    tenant_id, course_id = uuid4(), uuid4()
    teacher_id, other_teacher_id = uuid4(), uuid4()
    classroom_id, other_classroom_id = uuid4(), uuid4()
    class_id, other_class_id = uuid4(), uuid4()
    student_one, student_two, student_three = uuid4(), uuid4(), uuid4()
    admin_phone, registrar_phone, teacher_phone = (
        unique_phone(),
        unique_phone(),
        unique_phone(),
    )
    password_hash = hash_password("archive-password")

    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
            {"id": tenant_id, "name": f"档案机构-{uuid4().hex[:8]}"},
        )
        await connection.execute(
            text("INSERT INTO course (id, tenant_id, name) VALUES (:id, :tenant, '物理')"),
            {"id": course_id, "tenant": tenant_id},
        )
        for item_id, name in (
            (teacher_id, "任教老师"),
            (other_teacher_id, "其他老师"),
        ):
            await connection.execute(
                text("INSERT INTO teacher (id, tenant_id, name) VALUES (:id, :tenant, :name)"),
                {"id": item_id, "tenant": tenant_id, "name": name},
            )
        for item_id, name in ((classroom_id, "任教教室"), (other_classroom_id, "其他教室")):
            await connection.execute(
                text(
                    "INSERT INTO classroom (id, tenant_id, name, capacity) "
                    "VALUES (:id, :tenant, :name, 30)"
                ),
                {"id": item_id, "tenant": tenant_id, "name": name},
            )
        for item_id, name, teacher, room in (
            (class_id, "任教班级", teacher_id, classroom_id),
            (other_class_id, "其他班级", other_teacher_id, other_classroom_id),
        ):
            await connection.execute(
                text(
                    "INSERT INTO class "
                    "(id, tenant_id, course_id, teacher_id, classroom_id, name, capacity) "
                    "VALUES (:id, :tenant, :course, :teacher, :room, :name, 20)"
                ),
                {
                    "id": item_id,
                    "tenant": tenant_id,
                    "course": course_id,
                    "teacher": teacher,
                    "room": room,
                    "name": name,
                },
            )
        for item_id, name in (
            (student_one, "自己班学生"),
            (student_two, "别班学生"),
            (student_three, "未分班学生"),
        ):
            await connection.execute(
                text("INSERT INTO student (id, tenant_id, name) VALUES (:id, :tenant, :name)"),
                {"id": item_id, "tenant": tenant_id, "name": name},
            )
        for student, klass in ((student_one, class_id), (student_two, other_class_id)):
            await connection.execute(
                text(
                    "INSERT INTO class_enrollment (id, tenant_id, class_id, student_id, joined_at) "
                    "VALUES (:id, :tenant, :class, :student, current_date)"
                ),
                {"id": uuid4(), "tenant": tenant_id, "class": klass, "student": student},
            )
        for phone, role, linked_teacher in (
            (admin_phone, "admin", None),
            (registrar_phone, "registrar", None),
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
                    "teacher": linked_teacher,
                },
            )

    return ArchiveSeed(
        tenant_id=tenant_id,
        admin_phone=admin_phone,
        registrar_phone=registrar_phone,
        teacher_phone=teacher_phone,
        teacher_id=teacher_id,
        other_teacher_id=other_teacher_id,
        class_id=class_id,
        other_class_id=other_class_id,
        classroom_id=classroom_id,
        other_classroom_id=other_classroom_id,
        student_one=student_one,
        student_two=student_two,
        student_three=student_three,
    )


async def login(client: httpx.AsyncClient, phone: str) -> dict[str, str]:
    response = await client.post(
        "/auth/login", json={"phone": phone, "password": "archive-password"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_office_crud_and_archive_status(client, archive_seed: ArchiveSeed) -> None:
    headers = await login(client, archive_seed.admin_phone)

    created = await client.post(
        "/students",
        json={"name": "新学生", "grade": "八年级"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    student_id = created.json()["id"]

    listed = await client.get("/students", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] >= 4

    archived = await client.patch(
        f"/students/{student_id}", json={"status": "archived"}, headers=headers
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    parent = await client.post(
        "/parents",
        json={"name": "家长甲", "phone": unique_phone()},
        headers=headers,
    )
    assert parent.status_code == 201, parent.text
    duplicate = await client.post(
        "/parents",
        json={"name": "家长甲重复", "phone": parent.json()["phone"]},
        headers=headers,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "PARENT_DUPLICATED"

    teacher = await client.post(
        "/teachers", json={"name": "新老师", "subject": "化学"}, headers=headers
    )
    assert teacher.status_code == 201
    stopped = await client.patch(
        f"/teachers/{teacher.json()['id']}", json={"status": "inactive"}, headers=headers
    )
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "inactive"

    classroom = await client.post(
        "/classrooms",
        json={"name": "大教室", "capacity": 40, "available_slots": []},
        headers=headers,
    )
    assert classroom.status_code == 201, classroom.text
    invalid = await client.post(
        "/classrooms", json={"name": "错教室", "capacity": 0}, headers=headers
    )
    assert invalid.status_code == 422


async def test_teacher_scope_narrows_students_classes_and_rooms(
    client, archive_seed: ArchiveSeed
) -> None:
    headers = await login(client, archive_seed.teacher_phone)

    students = await client.get("/students", headers=headers)
    assert students.status_code == 200
    ids = {item["id"] for item in students.json()["items"]}
    assert str(archive_seed.student_one) in ids
    assert str(archive_seed.student_two) not in ids
    assert str(archive_seed.student_three) not in ids

    own = await client.get(f"/students/{archive_seed.student_one}", headers=headers)
    other = await client.get(f"/students/{archive_seed.student_two}", headers=headers)
    assert own.status_code == 200
    assert other.status_code == 403
    assert other.json()["error"]["code"] == "OUT_OF_SCOPE"

    classes = await client.get("/classes", headers=headers)
    assert [item["id"] for item in classes.json()["items"]] == [str(archive_seed.class_id)]

    rooms = await client.get("/classrooms", headers=headers)
    assert [item["id"] for item in rooms.json()["items"]] == [str(archive_seed.classroom_id)]

    me = await client.get("/teachers/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["id"] == str(archive_seed.teacher_id)

    office_me = await client.get(
        "/teachers/me", headers=await login(client, archive_seed.admin_phone)
    )
    assert office_me.status_code == 403


async def test_invite_code_binds_parent_and_resigns_token(
    client, archive_seed: ArchiveSeed
) -> None:
    admin = await login(client, archive_seed.admin_phone)
    invite = await client.post(
        "/invite-codes",
        json={"student_id": str(archive_seed.student_one), "expires_in_hours": 24},
        headers=admin,
    )
    assert invite.status_code == 201, invite.text
    code = invite.json()["code"]

    parent_openid = unique_openid("archive-parent")
    parent_login = await client.post("/auth/wechat-login", json={"wx_code": parent_openid})
    parent_token = parent_login.json()["access_token"]
    assert parent_login.json()["tenant_id"] is None

    bound = await client.post(
        "/me/bindings",
        json={"invite_code": code},
        headers={"Authorization": f"Bearer {parent_token}"},
    )
    assert bound.status_code == 201, bound.text
    assert bound.json()["student_id"] == str(archive_seed.student_one)
    assert bound.json()["tenant_id"] == str(archive_seed.tenant_id)
    parent_headers = {"Authorization": f"Bearer {bound.json()['access_token']}"}

    own = await client.get(f"/students/{archive_seed.student_one}", headers=parent_headers)
    sibling = await client.get(f"/students/{archive_seed.student_two}", headers=parent_headers)
    assert own.status_code == 200
    assert sibling.status_code == 403

    my_students = await client.get("/me/students", headers=parent_headers)
    assert my_students.status_code == 200
    assert [item["student_id"] for item in my_students.json()["items"]] == [
        str(archive_seed.student_one)
    ]

    reused = await client.post(
        "/me/bindings",
        json={"invite_code": code},
        headers={"Authorization": f"Bearer {parent_token}"},
    )
    assert reused.status_code == 404
    assert reused.json()["error"]["code"] == "INVITE_CODE_INVALID"

    history = await client.get(f"/students/{archive_seed.student_one}/invite-codes", headers=admin)
    assert history.status_code == 200
    assert history.json()["items"][0]["status"] == "used"
    assert history.json()["items"][0]["used_by"] is not None


async def test_expired_invite_is_rejected(client, archive_seed: ArchiveSeed) -> None:
    admin = await login(client, archive_seed.admin_phone)
    invite = await client.post(
        "/invite-codes",
        json={"student_id": str(archive_seed.student_three)},
        headers=admin,
    )
    engine = create_async_engine(get_settings().migration_database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE invite_code SET expires_at = :past WHERE code = :code"),
                {
                    "past": datetime.now(UTC) - timedelta(hours=1),
                    "code": invite.json()["code"],
                },
            )
    finally:
        await engine.dispose()

    login_response = await client.post(
        "/auth/wechat-login", json={"wx_code": unique_openid("expired-parent")}
    )
    response = await client.post(
        "/me/bindings",
        json={"invite_code": invite.json()["code"]},
        headers={"Authorization": f"Bearer {login_response.json()['access_token']}"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INVITE_CODE_INVALID"


async def test_cross_tenant_binding_and_invite_reads_are_empty(
    client, owner_engine, archive_seed: ArchiveSeed
) -> None:
    other_tenant, other_student = uuid4(), uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
            {"id": other_tenant, "name": f"隔离机构-{uuid4().hex[:8]}"},
        )
        await connection.execute(
            text("INSERT INTO student (id, tenant_id, name) VALUES (:id, :tenant, '他租学生')"),
            {"id": other_student, "tenant": other_tenant},
        )

    admin = await login(client, archive_seed.admin_phone)

    cross_read = await client.get(f"/students/{other_student}", headers=admin)
    assert cross_read.status_code == 404

    parent = await client.post(
        "/parents",
        json={"name": "本租家长", "phone": unique_phone()},
        headers=admin,
    )
    cross_bind = await client.post(
        "/bindings",
        json={"parent_id": parent.json()["id"], "student_id": str(other_student)},
        headers=admin,
    )
    assert cross_bind.status_code == 404
    assert cross_bind.json()["error"]["code"] == "STUDENT_NOT_FOUND"

    listings = await client.get(f"/bindings?student_id={other_student}", headers=admin)
    assert listings.status_code == 200
    assert listings.json()["items"] == []

    cross_invite = await client.post(
        "/invite-codes",
        json={"student_id": str(other_student)},
        headers=admin,
    )
    assert cross_invite.status_code == 404


async def test_archived_student_cannot_be_bound_or_invited(
    client, archive_seed: ArchiveSeed
) -> None:
    admin = await login(client, archive_seed.admin_phone)
    await client.patch(
        f"/students/{archive_seed.student_three}",
        json={"status": "archived"},
        headers=admin,
    )

    invite = await client.post(
        "/invite-codes",
        json={"student_id": str(archive_seed.student_three)},
        headers=admin,
    )
    assert invite.status_code == 422
    assert invite.json()["error"]["code"] == "STUDENT_ARCHIVED"

    parent = await client.post(
        "/parents",
        json={"name": "归档家长", "phone": unique_phone()},
        headers=admin,
    )
    direct = await client.post(
        "/bindings",
        json={
            "parent_id": parent.json()["id"],
            "student_id": str(archive_seed.student_three),
        },
        headers=admin,
    )
    assert direct.status_code == 422
