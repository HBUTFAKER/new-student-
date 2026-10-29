"""Course templates and class lifecycle (steps 2.1 / 2.2)."""

import os
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from app.core.security import decode_token, hash_password
from tests.auth_support import unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run course/class tests",
    ),
]

PASSWORD = "course-password"


@dataclass(frozen=True, slots=True)
class CourseSeed:
    tenant_id: UUID
    admin_phone: str
    teacher_phone: str
    other_teacher_phone: str
    teacher_id: UUID
    other_teacher_id: UUID
    big_room_id: UUID
    small_room_id: UUID


async def login(client: httpx.AsyncClient, phone: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"phone": phone, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def course_seed(owner_engine) -> CourseSeed:
    tenant_id = uuid4()
    teacher_id, other_teacher_id = uuid4(), uuid4()
    big_room_id, small_room_id = uuid4(), uuid4()
    admin_phone, teacher_phone, other_teacher_phone = (
        unique_phone(),
        unique_phone(),
        unique_phone(),
    )
    password_hash = hash_password(PASSWORD)

    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
            {"id": tenant_id, "name": f"课程机构-{uuid4().hex[:8]}"},
        )
        for item_id, name in ((teacher_id, "带班老师"), (other_teacher_id, "别班老师")):
            await connection.execute(
                text("INSERT INTO teacher (id, tenant_id, name) VALUES (:id, :tenant, :name)"),
                {"id": item_id, "tenant": tenant_id, "name": name},
            )
        for item_id, name, capacity in (
            (big_room_id, "大教室", 10),
            (small_room_id, "小教室", 5),
        ):
            await connection.execute(
                text(
                    "INSERT INTO classroom (id, tenant_id, name, capacity) "
                    "VALUES (:id, :tenant, :name, :capacity)"
                ),
                {"id": item_id, "tenant": tenant_id, "name": name, "capacity": capacity},
            )
        for phone, role, linked in (
            (admin_phone, "admin", None),
            (teacher_phone, "teacher", teacher_id),
            (other_teacher_phone, "teacher", other_teacher_id),
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

    return CourseSeed(
        tenant_id=tenant_id,
        admin_phone=admin_phone,
        teacher_phone=teacher_phone,
        other_teacher_phone=other_teacher_phone,
        teacher_id=teacher_id,
        other_teacher_id=other_teacher_id,
        big_room_id=big_room_id,
        small_room_id=small_room_id,
    )


async def create_course(
    client: httpx.AsyncClient, headers: dict[str, str], name: str = "初二物理"
) -> dict:
    response = await client.post(
        "/courses",
        json={"name": name, "subject": "物理", "duration_min": 90, "remark": "周五晚"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_class(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    seed: CourseSeed,
    *,
    course_id: str,
    teacher_id: UUID | None = None,
    classroom_id: UUID | None = None,
    capacity: int | None = None,
    name: str = "秋季A班",
) -> dict:
    today = date.today()
    payload = {
        "course_id": course_id,
        "teacher_id": str(teacher_id or seed.teacher_id),
        "classroom_id": str(classroom_id or seed.big_room_id),
        "name": name,
        "term_start": (today - timedelta(days=7)).isoformat(),
        "term_end": (today + timedelta(days=90)).isoformat(),
    }
    if capacity is not None:
        payload["capacity"] = capacity
    response = await client.post("/classes", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def create_student(client: httpx.AsyncClient, headers: dict[str, str], name: str) -> dict:
    response = await client.post("/students", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------------- course template


async def test_course_template_crud(client, course_seed: CourseSeed) -> None:
    headers = await login(client, course_seed.admin_phone)

    course = await create_course(client, headers)
    assert course["duration_min"] == 90
    assert course["status"] == "active"

    listing = await client.get("/courses", params={"keyword": "物理"}, headers=headers)
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["items"]] == [course["id"]]

    patched = await client.patch(
        f"/courses/{course['id']}",
        json={"status": "inactive", "remark": "暂停招生"},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "inactive"

    inactive = await client.get("/courses", params={"status": "inactive"}, headers=headers)
    assert [item["id"] for item in inactive.json()["items"]] == [course["id"]]

    removed = await client.delete(f"/courses/{course['id']}", headers=headers)
    assert removed.status_code == 204
    assert (await client.get(f"/courses/{course['id']}", headers=headers)).status_code == 404


async def test_course_in_use_cannot_be_deleted(
    client, owner_engine, course_seed: CourseSeed
) -> None:
    headers = await login(client, course_seed.admin_phone)
    course = await create_course(client, headers)
    klass = await create_class(client, headers, course_seed, course_id=course["id"])

    refused = await client.delete(f"/courses/{course['id']}", headers=headers)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "COURSE_IN_USE"

    # Nothing was touched: the course is still readable and the class still points at it.
    assert (await client.get(f"/courses/{course['id']}", headers=headers)).status_code == 200
    detail = await client.get(f"/classes/{klass['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["course_id"] == course["id"]

    async with owner_engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT count(*) FROM class WHERE course_id = :id"), {"id": course["id"]}
        )
        assert rows.scalar_one() == 1

    # Lifecycle replacement is a status change, not a delete.
    retired = await client.patch(
        f"/courses/{course['id']}", json={"status": "inactive"}, headers=headers
    )
    assert retired.status_code == 200


# -------------------------------------------------------------- class creation


async def test_class_creation_validates_refs_term_and_capacity(
    client, course_seed: CourseSeed
) -> None:
    headers = await login(client, course_seed.admin_phone)
    course = await create_course(client, headers)

    missing_course = await client.post(
        "/classes",
        json={
            "course_id": str(uuid4()),
            "teacher_id": str(course_seed.teacher_id),
            "classroom_id": str(course_seed.big_room_id),
            "name": "无课班",
            "term_start": date.today().isoformat(),
            "term_end": (date.today() + timedelta(days=30)).isoformat(),
        },
        headers=headers,
    )
    assert missing_course.status_code == 404
    assert missing_course.json()["error"]["code"] == "COURSE_NOT_FOUND"

    too_big = await client.post(
        "/classes",
        json={
            "course_id": course["id"],
            "teacher_id": str(course_seed.teacher_id),
            "classroom_id": str(course_seed.small_room_id),
            "name": "超容班",
            "term_start": date.today().isoformat(),
            "term_end": (date.today() + timedelta(days=30)).isoformat(),
            "capacity": 6,
        },
        headers=headers,
    )
    assert too_big.status_code == 422
    assert too_big.json()["error"]["code"] == "CAPACITY_INVALID"

    bad_term = await client.post(
        "/classes",
        json={
            "course_id": course["id"],
            "teacher_id": str(course_seed.teacher_id),
            "classroom_id": str(course_seed.big_room_id),
            "name": "倒序班",
            "term_start": (date.today() + timedelta(days=30)).isoformat(),
            "term_end": date.today().isoformat(),
        },
        headers=headers,
    )
    assert bad_term.status_code == 422
    assert bad_term.json()["error"]["code"] == "TERM_INVALID"

    # Omitting capacity inherits the room's capacity.
    klass = await create_class(client, headers, course_seed, course_id=course["id"])
    assert klass["capacity"] == 10
    assert klass["classroom_capacity"] == 10
    assert klass["enrolled"] == 0
    assert klass["teacher_name"] == "带班老师"
    assert klass["course_name"] == "初二物理"


async def test_class_patch_rechecks_capacity_and_roster_size(
    client, course_seed: CourseSeed
) -> None:
    headers = await login(client, course_seed.admin_phone)
    course = await create_course(client, headers)
    klass = await create_class(client, headers, course_seed, course_id=course["id"], capacity=8)

    for index in range(3):
        student = await create_student(client, headers, f"容量生{index}")
        enrolled = await client.post(
            f"/classes/{klass['id']}/students",
            json={"student_id": student["id"]},
            headers=headers,
        )
        assert enrolled.status_code == 201, enrolled.text

    # Moving to a smaller room whose capacity is below the class capacity.
    smaller = await client.patch(
        f"/classes/{klass['id']}",
        json={"classroom_id": str(course_seed.small_room_id)},
        headers=headers,
    )
    assert smaller.status_code == 422
    assert smaller.json()["error"]["code"] == "CAPACITY_INVALID"

    # Shrinking capacity below the number already enrolled.
    below_roster = await client.patch(
        f"/classes/{klass['id']}", json={"capacity": 2}, headers=headers
    )
    assert below_roster.status_code == 422
    assert below_roster.json()["error"]["code"] == "CAPACITY_EXCEEDED"

    swapped = await client.patch(
        f"/classes/{klass['id']}",
        json={"classroom_id": str(course_seed.small_room_id), "capacity": 5},
        headers=headers,
    )
    assert swapped.status_code == 200
    assert swapped.json()["classroom_name"] == "小教室"
    assert swapped.json()["enrolled"] == 3


# -------------------------------------------------------------------- roster


async def test_capacity_boundary_and_roster_rules(client, course_seed: CourseSeed) -> None:
    headers = await login(client, course_seed.admin_phone)
    course = await create_course(client, headers)
    klass = await create_class(client, headers, course_seed, course_id=course["id"], capacity=10)

    students = []
    for index in range(10):
        student = await create_student(client, headers, f"满员生{index}")
        students.append(student)
        response = await client.post(
            f"/classes/{klass['id']}/students",
            json={"student_id": student["id"]},
            headers=headers,
        )
        assert response.status_code == 201, response.text

    eleventh = await create_student(client, headers, "第十一人")
    rejected = await client.post(
        f"/classes/{klass['id']}/students",
        json={"student_id": eleventh["id"]},
        headers=headers,
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "CLASS_FULL"

    duplicate = await client.post(
        f"/classes/{klass['id']}/students",
        json={"student_id": students[0]["id"]},
        headers=headers,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "ALREADY_ENROLLED"

    archived = await create_student(client, headers, "归档生")
    archived_patch = await client.patch(
        f"/students/{archived['id']}", json={"status": "archived"}, headers=headers
    )
    assert archived_patch.status_code == 200
    inactive = await client.post(
        f"/classes/{klass['id']}/students",
        json={"student_id": archived["id"]},
        headers=headers,
    )
    assert inactive.status_code == 422
    assert inactive.json()["error"]["code"] == "STUDENT_STATUS_INVALID"

    roster = await client.get(f"/classes/{klass['id']}/students", headers=headers)
    assert roster.status_code == 200
    assert roster.json()["total"] == 10
    assert roster.json()["items"][0]["student_name"].startswith("满员生")

    left = await client.delete(
        f"/classes/{klass['id']}/students/{students[0]['id']}", headers=headers
    )
    assert left.status_code == 204
    again = await client.delete(
        f"/classes/{klass['id']}/students/{students[0]['id']}", headers=headers
    )
    assert again.status_code == 404
    assert again.json()["error"]["code"] == "ENROLLMENT_NOT_FOUND"

    # A freed seat is immediately available again.
    refilled = await client.post(
        f"/classes/{klass['id']}/students",
        json={"student_id": eleventh["id"]},
        headers=headers,
    )
    assert refilled.status_code == 201

    detail = await client.get(f"/classes/{klass['id']}", headers=headers)
    assert detail.json()["enrolled"] == 10


async def test_ended_class_rejects_enrolment(client, course_seed: CourseSeed) -> None:
    headers = await login(client, course_seed.admin_phone)
    course = await create_course(client, headers)
    klass = await create_class(client, headers, course_seed, course_id=course["id"])

    ended = await client.patch(f"/classes/{klass['id']}", json={"status": "ended"}, headers=headers)
    assert ended.status_code == 200

    student = await create_student(client, headers, "结课后学生")
    response = await client.post(
        f"/classes/{klass['id']}/students",
        json={"student_id": student["id"]},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CLASS_ENDED"


# ------------------------------------------------------------- teacher scope


async def test_teacher_scope_on_classes_and_roster(client, course_seed: CourseSeed) -> None:
    office = await login(client, course_seed.admin_phone)
    course = await create_course(client, office)
    own = await create_class(client, office, course_seed, course_id=course["id"], name="自家班")
    other = await create_class(
        client,
        office,
        course_seed,
        course_id=course["id"],
        teacher_id=course_seed.other_teacher_id,
        name="别家班",
    )
    student = await create_student(client, office, "自家学员")
    await client.post(
        f"/classes/{own['id']}/students", json={"student_id": student["id"]}, headers=office
    )

    teacher = await login(client, course_seed.teacher_phone)
    listing = await client.get("/classes", headers=teacher)
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["items"]] == [own["id"]]

    own_detail = await client.get(f"/classes/{own['id']}", headers=teacher)
    assert own_detail.status_code == 200
    foreign_detail = await client.get(f"/classes/{other['id']}", headers=teacher)
    assert foreign_detail.status_code == 404
    assert foreign_detail.json()["error"]["code"] == "CLASS_NOT_FOUND"

    own_roster = await client.get(f"/classes/{own['id']}/students", headers=teacher)
    assert own_roster.status_code == 200
    assert [item["student_name"] for item in own_roster.json()["items"]] == ["自家学员"]
    assert (
        await client.get(f"/classes/{other['id']}/students", headers=teacher)
    ).status_code == 404

    # Teachers cannot mutate the roster or the class itself.
    assert (await client.delete(f"/classes/{own['id']}", headers=teacher)).status_code == 405
    assert (
        await client.post(
            f"/classes/{own['id']}/students",
            json={"student_id": student["id"]},
            headers=teacher,
        )
    ).status_code == 403

    # Office still sees every class in the tenant.
    office_listing = await client.get("/classes", headers=office)
    ids = {item["id"] for item in office_listing.json()["items"]}
    assert {own["id"], other["id"]} <= ids


async def test_roster_changes_take_effect_without_cache(client, course_seed: CourseSeed) -> None:
    office = await login(client, course_seed.admin_phone)
    course = await create_course(client, office)
    klass = await create_class(client, office, course_seed, course_id=course["id"])
    student = await create_student(client, office, "进出班学员")
    await client.post(
        f"/classes/{klass['id']}/students", json={"student_id": student["id"]}, headers=office
    )

    # Bind a parent so we can prove the two scopes are independent and live.
    openid = f"course-parent-{uuid4().hex[:10]}"
    first = await client.post("/auth/wechat-login", json={"wx_code": openid})
    wechat_user_id = decode_token(first.json()["access_token"], expected_type="access").subject_id
    parent = await client.post(
        "/parents",
        json={
            "name": "进出班家长",
            "phone": unique_phone(),
            "wechat_user_id": str(wechat_user_id),
        },
        headers=office,
    )
    await client.post(
        "/bindings",
        json={"parent_id": parent.json()["id"], "student_id": student["id"]},
        headers=office,
    )
    parent_login = await client.post("/auth/wechat-login", json={"wx_code": openid})
    parent_headers = {"Authorization": f"Bearer {parent_login.json()['access_token']}"}

    teacher = await login(client, course_seed.teacher_phone)
    assert (await client.get(f"/students/{student['id']}", headers=teacher)).status_code == 200

    removed = await client.delete(
        f"/classes/{klass['id']}/students/{student['id']}", headers=office
    )
    assert removed.status_code == 204

    # Teacher scope recomputes on the next request: the student leaves the roster
    # and the teaching-class scope at once.
    roster = await client.get(f"/classes/{klass['id']}/students", headers=teacher)
    assert roster.json()["items"] == []
    assert (await client.get(f"/students/{student['id']}", headers=teacher)).status_code == 403
    teacher_students = await client.get("/students", headers=teacher)
    assert student["id"] not in {item["id"] for item in teacher_students.json()["items"]}

    # The parent binding is a different relation and still resolves live.
    assert (
        await client.get(f"/students/{student['id']}", headers=parent_headers)
    ).status_code == 200
    assert (await client.get("/me/students", headers=parent_headers)).status_code == 200


# ------------------------------------------------------------ tenant boundary


async def test_cross_tenant_course_and_class_are_invisible(client, owner_engine, course_seed):
    other_tenant, foreign_course, foreign_class = uuid4(), uuid4(), uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
            {"id": other_tenant, "name": f"别家机构-{uuid4().hex[:8]}"},
        )
        await connection.execute(
            text("INSERT INTO course (id, tenant_id, name) VALUES (:id, :tenant, '别家课程')"),
            {"id": foreign_course, "tenant": other_tenant},
        )
        await connection.execute(
            text(
                "INSERT INTO class "
                "(id, tenant_id, course_id, teacher_id, classroom_id, name) "
                "VALUES (:id, :tenant, :course, :teacher, :room, '别家班级')"
            ),
            {
                "id": foreign_class,
                "tenant": other_tenant,
                "course": foreign_course,
                "teacher": course_seed.teacher_id,
                "room": course_seed.big_room_id,
            },
        )

    headers = await login(client, course_seed.admin_phone)

    assert (await client.get(f"/courses/{foreign_course}", headers=headers)).status_code == 404
    assert (await client.get(f"/classes/{foreign_class}", headers=headers)).status_code == 404
    assert (
        await client.get(f"/classes/{foreign_class}/students", headers=headers)
    ).status_code == 404

    foreign_capacity = await client.post(
        "/classes",
        json={
            "course_id": str(foreign_course),
            "teacher_id": str(course_seed.teacher_id),
            "classroom_id": str(course_seed.big_room_id),
            "name": "偷用别家课程",
            "term_start": date.today().isoformat(),
            "term_end": (date.today() + timedelta(days=30)).isoformat(),
        },
        headers=headers,
    )
    assert foreign_capacity.status_code == 404

    courses = await client.get("/courses", headers=headers)
    assert str(foreign_course) not in {item["id"] for item in courses.json()["items"]}
    classes = await client.get("/classes", headers=headers)
    assert str(foreign_class) not in {item["id"] for item in classes.json()["items"]}

    # A class cannot be moved onto another tenant's teacher profile either.
    own_course = await create_course(client, headers)
    own_class = await create_class(client, headers, course_seed, course_id=own_course["id"])
    foreign_teacher = uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO teacher (id, tenant_id, name) VALUES (:id, :tenant, '别家老师')"),
            {"id": foreign_teacher, "tenant": other_tenant},
        )
    moved = await client.patch(
        f"/classes/{own_class['id']}",
        json={"teacher_id": str(foreign_teacher)},
        headers=headers,
    )
    assert moved.status_code == 404
    assert moved.json()["error"]["code"] == "TEACHER_NOT_FOUND"
