"""Check-in with lesson deduction (step 3.5) — the project's first red line.

What must never break:

* 签到与扣课同一事务，扣课只走 ``deduct_lessons``；
* 重复提交 / 断网重试 / 并发点击都只扣一次（唯一键是裁判，不是先查后插）；
* 请假/缺勤按 ``org_config.absence_deduct_rule``（none=0 / deduct=1）；
* 余额不足逐条 failed，不阻塞其他学生，也不留下 attendance 行；
* 老师只能给自己任教的班级签到；
* 3.4 的 ``makeup`` 预登记行在正式签到时原地转正。

The concurrency test really uses two connections (``AsyncSessionFactory`` +
``tenant_context``), not a sequential simulation.
"""

import asyncio
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from app.core.security import hash_password
from app.db.session import AsyncSessionFactory
from app.db.tenant import tenant_context
from app.services.attendance import CheckInRequest, check_in
from app.services.ledger import BUSINESS_TZ, business_today
from tests.auth_support import unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run attendance tests",
    ),
]

PASSWORD = "attendance-password"


@dataclass(frozen=True, slots=True)
class AttendanceSeed:
    tenant_id: UUID
    admin_id: UUID
    admin_phone: str
    teacher_phone: str
    teacher1_id: UUID
    teacher2_id: UUID
    classroom1_id: UUID
    classroom2_id: UUID
    course_id: UUID
    class_a_id: UUID
    class_b_id: UUID
    student1_id: UUID
    student2_id: UUID
    student3_id: UUID


async def login(client: httpx.AsyncClient, phone: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"phone": phone, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def attendance_seed(owner_engine) -> AttendanceSeed:
    tenant_id = uuid4()
    admin_id = uuid4()
    teacher1, teacher2 = uuid4(), uuid4()
    classroom1, classroom2 = uuid4(), uuid4()
    course_id = uuid4()
    class_a, class_b = uuid4(), uuid4()
    student1, student2, student3 = uuid4(), uuid4(), uuid4()
    admin_phone, teacher_phone = unique_phone(), unique_phone()
    password_hash = hash_password(PASSWORD)

    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
            {"id": tenant_id, "name": f"签到机构-{uuid4().hex[:8]}"},
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
        for class_id, teacher_id, classroom_id, name in (
            (class_a, teacher1, classroom1, "A班"),
            (class_b, teacher2, classroom2, "B班"),
        ):
            await connection.execute(
                text(
                    "INSERT INTO class (id, tenant_id, course_id, teacher_id, classroom_id, name) "
                    "VALUES (:id, :t, :c, :th, :r, :name)"
                ),
                {
                    "id": class_id,
                    "t": tenant_id,
                    "c": course_id,
                    "th": teacher_id,
                    "r": classroom_id,
                    "name": name,
                },
            )
        for student_id in (student1, student2, student3):
            await connection.execute(
                text("INSERT INTO student (id, tenant_id, name) VALUES (:id, :t, '学生')"),
                {"id": student_id, "t": tenant_id},
            )
        for class_id, student_id in (
            (class_a, student1),
            (class_a, student2),
            (class_a, student3),
        ):
            await connection.execute(
                text(
                    "INSERT INTO class_enrollment (id, tenant_id, class_id, student_id, joined_at) "
                    "VALUES (:id, :t, :c, :s, '2026-09-01')"
                ),
                {"id": uuid4(), "t": tenant_id, "c": class_id, "s": student_id},
            )
        await connection.execute(
            text(
                "INSERT INTO org_config (id, tenant_id, absence_deduct_rule) "
                "VALUES (:id, :t, 'none')"
            ),
            {"id": uuid4(), "t": tenant_id},
        )
        for staff_id, phone, role, teacher_id in (
            (admin_id, admin_phone, "admin", None),
            (uuid4(), teacher_phone, "teacher", teacher1),
        ):
            await connection.execute(
                text(
                    "INSERT INTO staff_account "
                    "(id, tenant_id, name, phone, password_hash, role, teacher_id, status) "
                    "VALUES (:id, :t, :name, :phone, :hash, :role, :teacher, 'active')"
                ),
                {
                    "id": staff_id,
                    "t": tenant_id,
                    "name": role,
                    "phone": phone,
                    "hash": password_hash,
                    "role": role,
                    "teacher": teacher_id,
                },
            )

    return AttendanceSeed(
        tenant_id=tenant_id,
        admin_id=admin_id,
        admin_phone=admin_phone,
        teacher_phone=teacher_phone,
        teacher1_id=teacher1,
        teacher2_id=teacher2,
        classroom1_id=classroom1,
        classroom2_id=classroom2,
        course_id=course_id,
        class_a_id=class_a,
        class_b_id=class_b,
        student1_id=student1,
        student2_id=student2,
        student3_id=student3,
    )


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=BUSINESS_TZ)


def past_day() -> date:
    return business_today() - timedelta(days=1)


def future_day() -> date:
    return business_today() + timedelta(days=2)


async def seed_lesson(
    owner_engine,
    seed: AttendanceSeed,
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
    owner_engine, seed: AttendanceSeed, *, lesson_id, student_id, status: str
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


async def buy(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    seed: AttendanceSeed,
    *,
    student_id: UUID,
    lesson_count: int,
    valid_months: int = 6,
) -> dict:
    response = await client.post(
        "/purchases",
        json={
            "student_id": str(student_id),
            "course_id": str(seed.course_id),
            "lesson_count": lesson_count,
            "valid_months": valid_months,
            "amount": "1000.00",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def mark(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    lesson_id: UUID,
    records: list[tuple[UUID, str]],
) -> httpx.Response:
    return await client.post(
        f"/lessons/{lesson_id}/attendance",
        json={
            "records": [
                {"student_id": str(student_id), "status": status} for student_id, status in records
            ]
        },
        headers=headers,
    )


async def consume_rows(
    owner_engine, *, student_id: UUID, lesson_id: UUID
) -> list[tuple[int, UUID]]:
    async with owner_engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT delta, purchase_id FROM lesson_ledger "
                "WHERE student_id = :s AND lesson_id = :l AND type = 'consume' "
                "ORDER BY created_at, id"
            ),
            {"s": student_id, "l": lesson_id},
        )
        return [(row[0], row[1]) for row in result]


# ------------------------------------------------------------ deduction rules


async def test_present_deducts_one_from_earliest_expiry_package(
    client, owner_engine, attendance_seed: AttendanceSeed
) -> None:
    seed = attendance_seed
    headers = await login(client, seed.admin_phone)
    early = await buy(
        client, headers, seed, student_id=seed.student1_id, lesson_count=1, valid_months=1
    )
    await buy(client, headers, seed, student_id=seed.student1_id, lesson_count=5, valid_months=6)
    lesson_id = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=at(past_day(), 18),
        end_at=at(past_day(), 19),
    )

    response = await mark(client, headers, lesson_id, [(seed.student1_id, "present")])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lesson_id"] == str(lesson_id)
    assert body["results"] == [
        {
            "student_id": str(seed.student1_id),
            "status": "present",
            "deduct_lessons": 1,
            "deduplicated": False,
            "reason": None,
        }
    ]

    rows = await consume_rows(owner_engine, student_id=seed.student1_id, lesson_id=lesson_id)
    # 先过期先扣：早到期的那一包（valid_months=1）先被扣，delta 是 -1。
    assert rows == [(-1, UUID(early["id"]))]


async def test_absence_rule_none_then_deduct(
    client, owner_engine, attendance_seed: AttendanceSeed
) -> None:
    seed = attendance_seed
    headers = await login(client, seed.admin_phone)
    await buy(client, headers, seed, student_id=seed.student2_id, lesson_count=3)
    lesson_id = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=at(past_day(), 18),
        end_at=at(past_day(), 19),
    )

    # 默认 none：请假不扣课，也不产生流水。
    default_rule = await client.get("/org-config/absence-deduct-rule", headers=headers)
    assert default_rule.status_code == 200, default_rule.text
    assert default_rule.json()["absence_deduct_rule"] == "none"

    leave = await mark(client, headers, lesson_id, [(seed.student2_id, "leave")])
    assert leave.status_code == 200, leave.text
    assert leave.json()["results"][0]["deduct_lessons"] == 0
    assert await consume_rows(owner_engine, student_id=seed.student2_id, lesson_id=lesson_id) == []

    # 改成 deduct 后，缺勤照扣 1，并且留痕到 updated_by。
    patched = await client.patch(
        "/org-config/absence-deduct-rule",
        json={"absence_deduct_rule": "deduct"},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["absence_deduct_rule"] == "deduct"
    assert patched.json()["updated_by"] == str(seed.admin_id)

    switched = await client.get("/org-config/absence-deduct-rule", headers=headers)
    assert switched.json()["absence_deduct_rule"] == "deduct"

    lesson2 = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=at(past_day(), 20),
        end_at=at(past_day(), 21),
    )
    absent = await mark(client, headers, lesson2, [(seed.student2_id, "absent")])
    assert absent.status_code == 200, absent.text
    assert absent.json()["results"][0]["deduct_lessons"] == 1
    rows = await consume_rows(owner_engine, student_id=seed.student2_id, lesson_id=lesson2)
    assert [row[0] for row in rows] == [-1]


async def test_rule_patch_guards(client, attendance_seed: AttendanceSeed) -> None:
    seed = attendance_seed
    headers = await login(client, seed.admin_phone)

    invalid = await client.patch(
        "/org-config/absence-deduct-rule",
        json={"absence_deduct_rule": "sometimes"},
        headers=headers,
    )
    assert invalid.status_code == 422, invalid.text
    assert invalid.json()["error"]["code"] == "RULE_VALUE_INVALID"

    teacher = await login(client, seed.teacher_phone)
    denied = await client.patch(
        "/org-config/absence-deduct-rule",
        json={"absence_deduct_rule": "deduct"},
        headers=teacher,
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "OUT_OF_SCOPE"

    # 老师可读（点名界面要提示会不会扣课）。
    readable = await client.get("/org-config/absence-deduct-rule", headers=teacher)
    assert readable.status_code == 200, readable.text


# --------------------------------------------------------------- idempotency


async def test_retry_five_times_deducts_once(
    client, owner_engine, attendance_seed: AttendanceSeed
) -> None:
    seed = attendance_seed
    headers = await login(client, seed.admin_phone)
    await buy(client, headers, seed, student_id=seed.student1_id, lesson_count=3)
    lesson_id = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=at(past_day(), 18),
        end_at=at(past_day(), 19),
    )

    # 模拟断网重试：同一请求连发 5 次。
    responses = [
        await mark(client, headers, lesson_id, [(seed.student1_id, "present")]) for _ in range(5)
    ]
    assert [response.status_code for response in responses] == [200] * 5
    deduplicated = [response.json()["results"][0]["deduplicated"] for response in responses]
    assert deduplicated.count(False) == 1, deduplicated

    rows = await consume_rows(owner_engine, student_id=seed.student1_id, lesson_id=lesson_id)
    assert sum(delta for delta, _ in rows) == -1
    assert len(rows) == 1


async def test_concurrent_check_in_deducts_once(
    client, owner_engine, attendance_seed: AttendanceSeed
) -> None:
    """真双连接并发：恰好一个扣课，另一个 deduplicated。"""

    seed = attendance_seed
    headers = await login(client, seed.admin_phone)
    await buy(client, headers, seed, student_id=seed.student1_id, lesson_count=3)
    lesson_id = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=at(past_day(), 18),
        end_at=at(past_day(), 19),
    )

    async def check_in_service():
        async with AsyncSessionFactory() as session:
            async with tenant_context(session, seed.tenant_id):
                return await check_in(
                    session,
                    tenant_id=seed.tenant_id,
                    lesson_id=lesson_id,
                    records=[CheckInRequest(student_id=seed.student1_id, status="present")],
                    operator_id=seed.admin_id,
                )

    results = await asyncio.gather(check_in_service(), check_in_service())
    outcomes = [result[0] for result in results]
    assert sorted(outcome.deduplicated for outcome in outcomes) == [False, True]

    rows = await consume_rows(owner_engine, student_id=seed.student1_id, lesson_id=lesson_id)
    assert sum(delta for delta, _ in rows) == -1
    assert len(rows) == 1


async def test_three_check_ins_deduct_three(
    client, owner_engine, attendance_seed: AttendanceSeed
) -> None:
    """验收线：签到 3 次扣 3 课时（三次不同课次，余额与流水同时对上）。"""

    seed = attendance_seed
    headers = await login(client, seed.admin_phone)
    await buy(client, headers, seed, student_id=seed.student1_id, lesson_count=5)

    deducted = 0
    for day, hour in ((past_day() - timedelta(days=1), 18), (past_day(), 18), (past_day(), 20)):
        lesson_id = await seed_lesson(
            owner_engine,
            seed,
            class_id=seed.class_a_id,
            classroom_id=seed.classroom1_id,
            teacher_id=seed.teacher1_id,
            start_at=at(day, hour),
            end_at=at(day, hour + 1),
        )
        response = await mark(client, headers, lesson_id, [(seed.student1_id, "present")])
        assert response.status_code == 200, response.text
        deducted += response.json()["results"][0]["deduct_lessons"]

    assert deducted == 3

    balance = await client.get(f"/students/{seed.student1_id}/balance", headers=headers)
    assert balance.status_code == 200, balance.text
    assert balance.json()["total"] == 2

    ledger = await client.get(
        f"/students/{seed.student1_id}/ledger", params={"type": "consume"}, headers=headers
    )
    assert ledger.json()["total"] == 3
    assert [entry["delta"] for entry in ledger.json()["items"]] == [-1, -1, -1]


async def test_different_students_are_independent(
    client, owner_engine, attendance_seed: AttendanceSeed
) -> None:
    seed = attendance_seed
    headers = await login(client, seed.admin_phone)
    await buy(client, headers, seed, student_id=seed.student1_id, lesson_count=2)
    await buy(client, headers, seed, student_id=seed.student2_id, lesson_count=2)
    lesson_id = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=at(past_day(), 18),
        end_at=at(past_day(), 19),
    )

    response = await mark(
        client, headers, lesson_id, [(seed.student1_id, "present"), (seed.student2_id, "present")]
    )
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert [item["deduct_lessons"] for item in results] == [1, 1]
    for student_id in (seed.student1_id, seed.student2_id):
        rows = await consume_rows(owner_engine, student_id=student_id, lesson_id=lesson_id)
        assert [delta for delta, _ in rows] == [-1]


# ------------------------------------------------------------ partial failure


async def test_balance_exhausted_fails_only_that_student(
    client, owner_engine, attendance_seed: AttendanceSeed
) -> None:
    seed = attendance_seed
    headers = await login(client, seed.admin_phone)
    await buy(client, headers, seed, student_id=seed.student1_id, lesson_count=1)
    lesson_id = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=at(past_day(), 18),
        end_at=at(past_day(), 19),
    )

    response = await mark(
        client, headers, lesson_id, [(seed.student1_id, "present"), (seed.student3_id, "present")]
    )
    assert response.status_code == 200, response.text
    results = {item["student_id"]: item for item in response.json()["results"]}
    assert results[str(seed.student1_id)]["deduct_lessons"] == 1
    failed = results[str(seed.student3_id)]
    assert failed["status"] == "failed"
    assert failed["reason"] == "BALANCE_EXHAUSTED"

    # failed 不写 attendance 行，所以名单里只有 student1。
    listed = await client.get(f"/lessons/{lesson_id}/attendance", headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["student_id"] == str(seed.student1_id)

    # 购课后重提同一批：student3 这次成功，student1 仍 deduplicated，不会被重扣。
    await buy(client, headers, seed, student_id=seed.student3_id, lesson_count=1)
    again = await mark(
        client, headers, lesson_id, [(seed.student1_id, "present"), (seed.student3_id, "present")]
    )
    assert again.status_code == 200, again.text
    again_results = {item["student_id"]: item for item in again.json()["results"]}
    assert again_results[str(seed.student1_id)]["deduplicated"] is True
    assert again_results[str(seed.student3_id)]["deduct_lessons"] == 1
    assert again_results[str(seed.student3_id)]["deduplicated"] is False

    rows = await consume_rows(owner_engine, student_id=seed.student3_id, lesson_id=lesson_id)
    assert sum(delta for delta, _ in rows) == -1


# ---------------------------------------------------------------- guardrails


async def test_teacher_scope_and_not_started(
    client, owner_engine, attendance_seed: AttendanceSeed
) -> None:
    seed = attendance_seed
    await buy(
        client,
        await login(client, seed.admin_phone),
        seed,
        student_id=seed.student1_id,
        lesson_count=3,
    )
    teacher = await login(client, seed.teacher_phone)

    foreign_lesson = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_b_id,
        classroom_id=seed.classroom2_id,
        teacher_id=seed.teacher2_id,
        start_at=at(past_day(), 18),
        end_at=at(past_day(), 19),
    )
    denied = await mark(client, teacher, foreign_lesson, [(seed.student1_id, "present")])
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "OUT_OF_SCOPE"

    future_lesson = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=at(future_day(), 18),
        end_at=at(future_day(), 19),
    )
    too_early = await mark(client, teacher, future_lesson, [(seed.student1_id, "present")])
    assert too_early.status_code == 422, too_early.text
    assert too_early.json()["error"]["code"] == "LESSON_NOT_STARTED"

    own_lesson = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=at(past_day(), 20),
        end_at=at(past_day(), 21),
    )
    allowed = await mark(client, teacher, own_lesson, [(seed.student1_id, "present")])
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["results"][0]["deduct_lessons"] == 1


async def test_cancelled_lesson_rejects_check_in(
    client, owner_engine, attendance_seed: AttendanceSeed
) -> None:
    seed = attendance_seed
    headers = await login(client, seed.admin_phone)
    await buy(client, headers, seed, student_id=seed.student1_id, lesson_count=2)
    lesson_id = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=at(past_day(), 18),
        end_at=at(past_day(), 19),
        status="cancelled",
    )

    response = await mark(client, headers, lesson_id, [(seed.student1_id, "present")])
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "LESSON_CANCELLED"
    assert await consume_rows(owner_engine, student_id=seed.student1_id, lesson_id=lesson_id) == []


async def test_cross_tenant_lesson_is_404(
    client, owner_engine, attendance_seed: AttendanceSeed
) -> None:
    seed = attendance_seed
    headers = await login(client, seed.admin_phone)

    other_tenant, other_teacher, other_room = uuid4(), uuid4(), uuid4()
    other_course, other_class, other_lesson = uuid4(), uuid4(), uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
            {"id": other_tenant, "name": f"别家机构-{uuid4().hex[:8]}"},
        )
        await connection.execute(
            text("INSERT INTO teacher (id, tenant_id, name) VALUES (:id, :t, '别家老师')"),
            {"id": other_teacher, "t": other_tenant},
        )
        await connection.execute(
            text(
                "INSERT INTO classroom (id, tenant_id, name, capacity) "
                "VALUES (:id, :t, '别家教室', 10)"
            ),
            {"id": other_room, "t": other_tenant},
        )
        await connection.execute(
            text("INSERT INTO course (id, tenant_id, name) VALUES (:id, :t, '别家课程')"),
            {"id": other_course, "t": other_tenant},
        )
        await connection.execute(
            text(
                "INSERT INTO class (id, tenant_id, course_id, teacher_id, classroom_id, name) "
                "VALUES (:id, :t, :c, :th, :r, '别家班级')"
            ),
            {
                "id": other_class,
                "t": other_tenant,
                "c": other_course,
                "th": other_teacher,
                "r": other_room,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO lesson "
                "(id, tenant_id, class_id, classroom_id, teacher_id, start_at, end_at, status) "
                "VALUES (:id, :t, :c, :r, :th, now() - interval '2 hours', "
                "now() - interval '1 hour', 'normal')"
            ),
            {
                "id": other_lesson,
                "t": other_tenant,
                "c": other_class,
                "r": other_room,
                "th": other_teacher,
            },
        )

    response = await mark(client, headers, other_lesson, [(seed.student1_id, "present")])
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "LESSON_NOT_FOUND"


# --------------------------------------------------------------- makeup seats


async def test_makeup_seat_is_promoted_in_place(
    client, owner_engine, attendance_seed: AttendanceSeed
) -> None:
    seed = attendance_seed
    headers = await login(client, seed.admin_phone)
    await buy(client, headers, seed, student_id=seed.student1_id, lesson_count=2)
    lesson_id = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=at(past_day(), 18),
        end_at=at(past_day(), 19),
    )
    # 3.4 排的补课占位行：不是最终结果，正式签到要原地转正。
    await seed_attendance(
        owner_engine, seed, lesson_id=lesson_id, student_id=seed.student1_id, status="makeup"
    )

    promoted = await mark(client, headers, lesson_id, [(seed.student1_id, "present")])
    assert promoted.status_code == 200, promoted.text
    result = promoted.json()["results"][0]
    assert result["deduplicated"] is False
    assert result["status"] == "present"
    assert result["deduct_lessons"] == 1

    listed = await client.get(f"/lessons/{lesson_id}/attendance", headers=headers)
    items = listed.json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "present"
    assert items[0]["deduct_lessons"] == 1

    # 再提交一次就是最终状态 → deduplicated，且不再扣课。
    again = await mark(client, headers, lesson_id, [(seed.student1_id, "present")])
    assert again.json()["results"][0]["deduplicated"] is True
    rows = await consume_rows(owner_engine, student_id=seed.student1_id, lesson_id=lesson_id)
    assert sum(delta for delta, _ in rows) == -1
