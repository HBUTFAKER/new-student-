"""Lesson operations (step 3.4): single add, move/cancel, makeup scheduling.

Covers the red lines: move/cancel/makeup never write a ``lesson_ledger`` row;
already-checked-in or already-started lessons cannot be adjusted; a move that
clashes surfaces 409 with conflict detail; cancelling releases the slot so the
same classroom+teacher slot can be re-booked; makeup seats an absent student on
a future lesson and is idempotent.
"""

import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from app.core.security import hash_password
from app.services.ledger import BUSINESS_TZ, business_today
from tests.auth_support import unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run lesson ops tests",
    ),
]

PASSWORD = "ops-password"


@dataclass(frozen=True, slots=True)
class OpsSeed:
    tenant_id: UUID
    admin_phone: str
    teacher1_id: UUID
    teacher2_id: UUID
    classroom1_id: UUID
    classroom2_id: UUID
    class_a_id: UUID
    class_b_id: UUID
    student1_id: UUID
    student2_id: UUID


async def login(client: httpx.AsyncClient, phone: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"phone": phone, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def ops_seed(owner_engine) -> OpsSeed:
    tenant_id = uuid4()
    teacher1, teacher2 = uuid4(), uuid4()
    classroom1, classroom2 = uuid4(), uuid4()
    course_id = uuid4()
    class_a, class_b = uuid4(), uuid4()
    student1, student2 = uuid4(), uuid4()
    admin_phone = unique_phone()
    password_hash = hash_password(PASSWORD)

    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
            {"id": tenant_id, "name": f"课次操作机构-{uuid4().hex[:8]}"},
        )
        for teacher_id in (teacher1, teacher2):
            await connection.execute(
                text("INSERT INTO teacher (id, tenant_id, name) VALUES (:id, :t, '老师')"),
                {"id": teacher_id, "t": tenant_id},
            )
        for classroom_id in (classroom1, classroom2):
            await connection.execute(
                text(
                    "INSERT INTO classroom (id, tenant_id, name, capacity) "
                    "VALUES (:id, :t, '教室', 20)"
                ),
                {"id": classroom_id, "t": tenant_id},
            )
        await connection.execute(
            text("INSERT INTO course (id, tenant_id, name) VALUES (:id, :t, '课程')"),
            {"id": course_id, "t": tenant_id},
        )
        for class_id, teacher_id, classroom_id in (
            (class_a, teacher1, classroom1),
            (class_b, teacher2, classroom2),
        ):
            await connection.execute(
                text(
                    "INSERT INTO class (id, tenant_id, course_id, teacher_id, classroom_id, name) "
                    "VALUES (:id, :t, :c, :th, :r, '班级')"
                ),
                {
                    "id": class_id,
                    "t": tenant_id,
                    "c": course_id,
                    "th": teacher_id,
                    "r": classroom_id,
                },
            )
        for student_id in (student1, student2):
            await connection.execute(
                text("INSERT INTO student (id, tenant_id, name) VALUES (:id, :t, '学生')"),
                {"id": student_id, "t": tenant_id},
            )
        # student1 只在 A 班，student2 只在 B 班。
        for class_id, student_id in ((class_a, student1), (class_b, student2)):
            await connection.execute(
                text(
                    "INSERT INTO class_enrollment (id, tenant_id, class_id, student_id, joined_at) "
                    "VALUES (:id, :t, :c, :s, '2026-09-01')"
                ),
                {"id": uuid4(), "t": tenant_id, "c": class_id, "s": student_id},
            )
        await connection.execute(
            text(
                "INSERT INTO staff_account "
                "(id, tenant_id, name, phone, password_hash, role, status) "
                "VALUES (:id, :t, '管理员', :phone, :hash, 'admin', 'active')"
            ),
            {"id": uuid4(), "t": tenant_id, "phone": admin_phone, "hash": password_hash},
        )

    return OpsSeed(
        tenant_id=tenant_id,
        admin_phone=admin_phone,
        teacher1_id=teacher1,
        teacher2_id=teacher2,
        classroom1_id=classroom1,
        classroom2_id=classroom2,
        class_a_id=class_a,
        class_b_id=class_b,
        student1_id=student1,
        student2_id=student2,
    )


def slot(day: date, sh: int, sm: int, eh: int, em: int) -> tuple[datetime, datetime]:
    return (
        datetime.combine(day, time(sh, sm), tzinfo=BUSINESS_TZ),
        datetime.combine(day, time(eh, em), tzinfo=BUSINESS_TZ),
    )


def future_day() -> date:
    return business_today() + timedelta(days=3)


async def seed_lesson(
    owner_engine,
    seed: OpsSeed,
    *,
    class_id,
    classroom_id,
    teacher_id,
    start_at,
    end_at,
    status: str = "normal",
) -> UUID:
    lesson_id = uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO lesson "
                "(id, tenant_id, class_id, classroom_id, teacher_id, start_at, end_at, status) "
                "VALUES (:id, :t, :c, :r, :th, :s, :e, :st)"
            ),
            {
                "id": lesson_id,
                "t": seed.tenant_id,
                "c": class_id,
                "r": classroom_id,
                "th": teacher_id,
                "s": start_at,
                "e": end_at,
                "st": status,
            },
        )
    return lesson_id


async def seed_attendance(
    owner_engine, seed: OpsSeed, *, lesson_id, student_id, status: str
) -> None:
    async with owner_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO attendance "
                "(id, tenant_id, lesson_id, student_id, status, deduct_lessons, operator_id) "
                "VALUES (:id, :t, :l, :s, :st, 0, :op)"
            ),
            {
                "id": uuid4(),
                "t": seed.tenant_id,
                "l": lesson_id,
                "s": student_id,
                "st": status,
                "op": uuid4(),
            },
        )


async def ledger_count(owner_engine, tenant_id: UUID) -> int:
    async with owner_engine.connect() as connection:
        return (
            await connection.execute(
                text("SELECT count(*) FROM lesson_ledger WHERE tenant_id = :t"), {"t": tenant_id}
            )
        ).scalar_one()


async def create_via_api(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    seed: OpsSeed,
    *,
    class_id,
    classroom_id,
    teacher_id,
    start_at,
    end_at,
) -> httpx.Response:
    return await client.post(
        "/lessons",
        json={
            "class_id": str(class_id),
            "classroom_id": str(classroom_id),
            "teacher_id": str(teacher_id),
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
        },
        headers=headers,
    )


async def test_create_lesson_succeeds_with_names(client, ops_seed: OpsSeed) -> None:
    seed = ops_seed
    headers = await login(client, seed.admin_phone)
    start, end = slot(future_day(), 18, 0, 19, 0)

    response = await create_via_api(
        client,
        headers,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=start,
        end_at=end,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "normal"
    assert body["class_name"] == "班级"
    assert body["teacher_name"] == "老师"
    assert body["classroom_name"] == "教室"
    assert "+08:00" in body["start_at"]


async def test_create_lesson_rejects_naive_time(client, ops_seed: OpsSeed) -> None:
    seed = ops_seed
    headers = await login(client, seed.admin_phone)
    start, end = slot(future_day(), 18, 0, 19, 0)

    response = await client.post(
        "/lessons",
        json={
            "class_id": str(seed.class_a_id),
            "classroom_id": str(seed.classroom1_id),
            "teacher_id": str(seed.teacher1_id),
            "start_at": start.replace(tzinfo=None).isoformat(),
            "end_at": end.replace(tzinfo=None).isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_move_lesson_sets_moved_status(client, ops_seed: OpsSeed) -> None:
    seed = ops_seed
    headers = await login(client, seed.admin_phone)
    day = future_day()

    created = await create_via_api(
        client,
        headers,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=slot(day, 18, 0, 19, 0)[0],
        end_at=slot(day, 18, 0, 19, 0)[1],
    )
    assert created.status_code == 201, created.text
    lesson_id = created.json()["id"]

    new_start, new_end = slot(day, 20, 0, 21, 0)
    moved = await client.patch(
        f"/lessons/{lesson_id}",
        json={"start_at": new_start.isoformat(), "end_at": new_end.isoformat()},
        headers=headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["status"] == "moved"
    assert moved.json()["start_at"][:10] == new_start.date().isoformat()
    assert moved.json()["start_at"][11:16] == "20:00"


async def test_move_checked_in_lesson_is_rejected(client, owner_engine, ops_seed: OpsSeed) -> None:
    seed = ops_seed
    headers = await login(client, seed.admin_phone)
    start, end = slot(future_day(), 18, 0, 19, 0)
    lesson_id = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=start,
        end_at=end,
    )
    await seed_attendance(
        owner_engine, seed, lesson_id=lesson_id, student_id=seed.student1_id, status="present"
    )

    response = await client.patch(
        f"/lessons/{lesson_id}",
        json={"start_at": slot(future_day(), 20, 0, 21, 0)[0].isoformat()},
        headers=headers,
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "LESSON_ALREADY_DONE"


async def test_move_past_lesson_is_rejected(client, owner_engine, ops_seed: OpsSeed) -> None:
    seed = ops_seed
    headers = await login(client, seed.admin_phone)
    past = business_today() - timedelta(days=1)
    lesson_id = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=slot(past, 18, 0, 19, 0)[0],
        end_at=slot(past, 18, 0, 19, 0)[1],
    )

    response = await client.patch(
        f"/lessons/{lesson_id}",
        json={"start_at": slot(future_day(), 20, 0, 21, 0)[0].isoformat()},
        headers=headers,
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "LESSON_ALREADY_DONE"


async def test_move_into_conflict_returns_409_with_detail(client, ops_seed: OpsSeed) -> None:
    seed = ops_seed
    headers = await login(client, seed.admin_phone)
    day = future_day()

    first = await create_via_api(
        client,
        headers,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=slot(day, 18, 0, 19, 0)[0],
        end_at=slot(day, 18, 0, 19, 0)[1],
    )
    assert first.status_code == 201, first.text

    second = await create_via_api(
        client,
        headers,
        seed,
        class_id=seed.class_b_id,
        classroom_id=seed.classroom2_id,
        teacher_id=seed.teacher2_id,
        start_at=slot(day, 20, 0, 21, 0)[0],
        end_at=slot(day, 20, 0, 21, 0)[1],
    )
    assert second.status_code == 201, second.text

    # 把第二节移到第一节的时间段，并换成第一节的老师+教室 → 老师/教室双冲突。
    clash_start, clash_end = slot(day, 18, 30, 19, 30)
    response = await client.patch(
        f"/lessons/{second.json()['id']}",
        json={
            "start_at": clash_start.isoformat(),
            "end_at": clash_end.isoformat(),
            "classroom_id": str(seed.classroom1_id),
            "teacher_id": str(seed.teacher1_id),
        },
        headers=headers,
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "LESSON_OVERLAP"
    kinds = {c["type"] for c in response.json()["error"]["conflicts"]}
    assert {"teacher", "classroom"} <= kinds


async def test_cancel_releases_slot(client, ops_seed: OpsSeed) -> None:
    seed = ops_seed
    headers = await login(client, seed.admin_phone)
    day = future_day()
    start, end = slot(day, 18, 0, 19, 0)

    created = await create_via_api(
        client,
        headers,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=start,
        end_at=end,
    )
    assert created.status_code == 201, created.text
    lesson_id = created.json()["id"]

    cancelled = await client.patch(
        f"/lessons/{lesson_id}", json={"status": "cancelled"}, headers=headers
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"

    # 取消后同老师同教室同时段可再排。
    again = await create_via_api(
        client,
        headers,
        seed,
        class_id=seed.class_b_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=start,
        end_at=end,
    )
    assert again.status_code == 201, again.text


async def test_makeup_schedule_success(client, owner_engine, ops_seed: OpsSeed) -> None:
    seed = ops_seed
    headers = await login(client, seed.admin_phone)
    past = business_today() - timedelta(days=1)
    absent_lesson = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=slot(past, 18, 0, 19, 0)[0],
        end_at=slot(past, 18, 0, 19, 0)[1],
    )
    await seed_attendance(
        owner_engine, seed, lesson_id=absent_lesson, student_id=seed.student1_id, status="absent"
    )

    target_lesson = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_b_id,
        classroom_id=seed.classroom2_id,
        teacher_id=seed.teacher2_id,
        start_at=slot(future_day(), 18, 0, 19, 0)[0],
        end_at=slot(future_day(), 18, 0, 19, 0)[1],
    )

    response = await client.post(
        "/makeup-lessons",
        json={
            "student_id": str(seed.student1_id),
            "absent_lesson_id": str(absent_lesson),
            "target_lesson_id": str(target_lesson),
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "makeup"
    assert response.json()["target_lesson_id"] == str(target_lesson)


async def test_makeup_duplicate_schedule_409(client, owner_engine, ops_seed: OpsSeed) -> None:
    seed = ops_seed
    headers = await login(client, seed.admin_phone)
    past = business_today() - timedelta(days=1)
    absent_lesson = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=slot(past, 18, 0, 19, 0)[0],
        end_at=slot(past, 18, 0, 19, 0)[1],
    )
    await seed_attendance(
        owner_engine, seed, lesson_id=absent_lesson, student_id=seed.student1_id, status="leave"
    )
    target_lesson = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_b_id,
        classroom_id=seed.classroom2_id,
        teacher_id=seed.teacher2_id,
        start_at=slot(future_day(), 18, 0, 19, 0)[0],
        end_at=slot(future_day(), 18, 0, 19, 0)[1],
    )
    payload = {
        "student_id": str(seed.student1_id),
        "absent_lesson_id": str(absent_lesson),
        "target_lesson_id": str(target_lesson),
    }
    first = await client.post("/makeup-lessons", json=payload, headers=headers)
    assert first.status_code == 201, first.text

    second = await client.post("/makeup-lessons", json=payload, headers=headers)
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "MAKEUP_ALREADY_SCHEDULED"


async def test_makeup_requires_absence_422(client, owner_engine, ops_seed: OpsSeed) -> None:
    seed = ops_seed
    headers = await login(client, seed.admin_phone)
    absent_lesson = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=slot(business_today() - timedelta(days=1), 18, 0, 19, 0)[0],
        end_at=slot(business_today() - timedelta(days=1), 18, 0, 19, 0)[1],
    )
    target_lesson = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_b_id,
        classroom_id=seed.classroom2_id,
        teacher_id=seed.teacher2_id,
        start_at=slot(future_day(), 18, 0, 19, 0)[0],
        end_at=slot(future_day(), 18, 0, 19, 0)[1],
    )

    response = await client.post(
        "/makeup-lessons",
        json={
            "student_id": str(seed.student1_id),
            "absent_lesson_id": str(absent_lesson),
            "target_lesson_id": str(target_lesson),
        },
        headers=headers,
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "ABSENCE_NOT_FOUND"


async def test_cross_tenant_lesson_and_student_are_404(
    client, owner_engine, ops_seed: OpsSeed
) -> None:
    seed = ops_seed
    headers = await login(client, seed.admin_phone)

    other_tenant, foreign_teacher, foreign_room = uuid4(), uuid4(), uuid4()
    foreign_course, foreign_class, foreign_lesson, foreign_student = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
            {"id": other_tenant, "name": f"别家机构-{uuid4().hex[:8]}"},
        )
        await connection.execute(
            text("INSERT INTO teacher (id, tenant_id, name) VALUES (:id, :t, '别家老师')"),
            {"id": foreign_teacher, "t": other_tenant},
        )
        await connection.execute(
            text(
                "INSERT INTO classroom (id, tenant_id, name, capacity) "
                "VALUES (:id, :t, '别家教室', 10)"
            ),
            {"id": foreign_room, "t": other_tenant},
        )
        await connection.execute(
            text("INSERT INTO course (id, tenant_id, name) VALUES (:id, :t, '别家课程')"),
            {"id": foreign_course, "t": other_tenant},
        )
        await connection.execute(
            text(
                "INSERT INTO class (id, tenant_id, course_id, teacher_id, classroom_id, name) "
                "VALUES (:id, :t, :c, :th, :r, '别家班级')"
            ),
            {
                "id": foreign_class,
                "t": other_tenant,
                "c": foreign_course,
                "th": foreign_teacher,
                "r": foreign_room,
            },
        )
        await connection.execute(
            text("INSERT INTO student (id, tenant_id, name) VALUES (:id, :t, '别家学生')"),
            {"id": foreign_student, "t": other_tenant},
        )
        await connection.execute(
            text(
                "INSERT INTO lesson "
                "(id, tenant_id, class_id, classroom_id, teacher_id, start_at, end_at, status) "
                "VALUES (:id, :t, :c, :r, :th, now() + interval '1 hour', "
                "now() + interval '2 hours', 'normal')"
            ),
            {
                "id": foreign_lesson,
                "t": other_tenant,
                "c": foreign_class,
                "r": foreign_room,
                "th": foreign_teacher,
            },
        )

    patch = await client.patch(
        f"/lessons/{foreign_lesson}",
        json={"start_at": slot(future_day(), 20, 0, 21, 0)[0].isoformat()},
        headers=headers,
    )
    assert patch.status_code == 404, patch.text
    assert patch.json()["error"]["code"] == "LESSON_NOT_FOUND"

    makeup = await client.post(
        "/makeup-lessons",
        json={
            "student_id": str(foreign_student),
            "absent_lesson_id": str(foreign_lesson),
            "target_lesson_id": str(foreign_lesson),
        },
        headers=headers,
    )
    assert makeup.status_code == 404, makeup.text
    assert makeup.json()["error"]["code"] == "STUDENT_NOT_FOUND"


async def test_move_cancel_makeup_never_touch_ledger(
    client, owner_engine, ops_seed: OpsSeed
) -> None:
    """红线：调课 / 取消 / 补课排期全程不产生任何 lesson_ledger 流水。"""

    seed = ops_seed
    headers = await login(client, seed.admin_phone)
    day = future_day()

    created = await create_via_api(
        client,
        headers,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=slot(day, 18, 0, 19, 0)[0],
        end_at=slot(day, 18, 0, 19, 0)[1],
    )
    assert created.status_code == 201, created.text
    lesson_id = created.json()["id"]

    moved = await client.patch(
        f"/lessons/{lesson_id}",
        json={
            "start_at": slot(day, 20, 0, 21, 0)[0].isoformat(),
            "end_at": slot(day, 20, 0, 21, 0)[1].isoformat(),
        },
        headers=headers,
    )
    assert moved.status_code == 200, moved.text

    cancelled = await client.patch(
        f"/lessons/{lesson_id}", json={"status": "cancelled"}, headers=headers
    )
    assert cancelled.status_code == 200, cancelled.text

    past = business_today() - timedelta(days=1)
    absent_lesson = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=slot(past, 18, 0, 19, 0)[0],
        end_at=slot(past, 18, 0, 19, 0)[1],
    )
    await seed_attendance(
        owner_engine, seed, lesson_id=absent_lesson, student_id=seed.student1_id, status="absent"
    )
    target_lesson = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_b_id,
        classroom_id=seed.classroom2_id,
        teacher_id=seed.teacher2_id,
        start_at=slot(day, 18, 0, 19, 0)[0],
        end_at=slot(day, 18, 0, 19, 0)[1],
    )
    makeup = await client.post(
        "/makeup-lessons",
        json={
            "student_id": str(seed.student1_id),
            "absent_lesson_id": str(absent_lesson),
            "target_lesson_id": str(target_lesson),
        },
        headers=headers,
    )
    assert makeup.status_code == 201, makeup.text

    assert await ledger_count(owner_engine, seed.tenant_id) == 0
