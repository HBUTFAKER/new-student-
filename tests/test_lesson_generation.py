"""Lesson batch generation from schedule rules (step 3.2).

Dates are anchored to "tomorrow" so the assertions hold on any run date: the
generation window starts at ``max(rule.start_date, today)``, and a window that
begins exactly on the rule's start date contains exactly ``weeks`` occurrences
of every weekday.
"""

import os
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from app.core.security import hash_password
from app.services.ledger import business_today
from tests.auth_support import unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run lesson generation tests",
    ),
]

PASSWORD = "lesson-password"


@dataclass(frozen=True, slots=True)
class LessonSeed:
    tenant_id: UUID
    admin_phone: str
    teacher_phone: str
    teacher_id: UUID
    classroom_id: UUID
    class_id: UUID
    empty_class_id: UUID
    ended_class_id: UUID


async def login(client: httpx.AsyncClient, phone: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"phone": phone, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def lesson_seed(owner_engine) -> LessonSeed:
    tenant_id = uuid4()
    teacher_id, classroom_id, course_id = uuid4(), uuid4(), uuid4()
    class_id, empty_class_id, ended_class_id = uuid4(), uuid4(), uuid4()
    admin_phone, teacher_phone = unique_phone(), unique_phone()
    password_hash = hash_password(PASSWORD)

    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
            {"id": tenant_id, "name": f"课次机构-{uuid4().hex[:8]}"},
        )
        await connection.execute(
            text("INSERT INTO teacher (id, tenant_id, name) VALUES (:id, :tenant, '排课老师')"),
            {"id": teacher_id, "tenant": tenant_id},
        )
        await connection.execute(
            text(
                "INSERT INTO classroom (id, tenant_id, name, capacity) "
                "VALUES (:id, :tenant, '多媒体教室', 20)"
            ),
            {"id": classroom_id, "tenant": tenant_id},
        )
        await connection.execute(
            text("INSERT INTO course (id, tenant_id, name) VALUES (:id, :tenant, '初二物理')"),
            {"id": course_id, "tenant": tenant_id},
        )
        for item_id, name, status in (
            (class_id, "初二物理冲刺班", "active"),
            (empty_class_id, "待排课班", "active"),
            (ended_class_id, "去年秋季班", "ended"),
        ):
            await connection.execute(
                text(
                    "INSERT INTO class "
                    "(id, tenant_id, course_id, teacher_id, classroom_id, name, "
                    " term_start, term_end, status) "
                    "VALUES (:id, :tenant, :course, :teacher, :room, :name, "
                    " '2026-09-01', '2027-06-30', :status)"
                ),
                {
                    "id": item_id,
                    "tenant": tenant_id,
                    "course": course_id,
                    "teacher": teacher_id,
                    "room": classroom_id,
                    "name": name,
                    "status": status,
                },
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

    return LessonSeed(
        tenant_id=tenant_id,
        admin_phone=admin_phone,
        teacher_phone=teacher_phone,
        teacher_id=teacher_id,
        classroom_id=classroom_id,
        class_id=class_id,
        empty_class_id=empty_class_id,
        ended_class_id=ended_class_id,
    )


def schedule_start() -> date:
    """明天：一定晚于 today，所以生成窗口从规律 start_date 起算。"""

    return business_today() + timedelta(days=1)


def two_weekdays(start: date) -> list[int]:
    """起算日当天 + 隔一天（形如「周二四」），保证 8 周内各出现 8 次。"""

    return [start.weekday(), (start.weekday() + 2) % 7]


def expected_dates(start: date, weekdays: list[int]) -> set[str]:
    return {
        (start + timedelta(days=offset)).isoformat()
        for offset in range(8 * 7)
        if (start + timedelta(days=offset)).weekday() in weekdays
    }


async def create_rule(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    seed: LessonSeed,
    *,
    weekdays: list[int],
    start: date,
    weeks: int = 8,
) -> dict:
    response = await client.post(
        "/schedule-rules",
        json={
            "class_id": str(seed.class_id),
            "classroom_id": str(seed.classroom_id),
            "teacher_id": str(seed.teacher_id),
            "weekdays": weekdays,
            "start_time": "18:00:00",
            "end_time": "19:30:00",
            "start_date": start.isoformat(),
            "weeks": weeks,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def generate(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    seed: LessonSeed,
    *,
    class_id: UUID | None = None,
    weeks: int = 8,
) -> httpx.Response:
    return await client.post(
        "/lessons/generate",
        json={"class_id": str(class_id or seed.class_id), "weeks": weeks},
        headers=headers,
    )


async def list_lessons(
    client: httpx.AsyncClient, headers: dict[str, str], seed: LessonSeed, **params
) -> dict:
    query = {"class_id": str(seed.class_id), "page_size": 100, **params}
    response = await client.get("/lessons", params=query, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_two_days_eight_weeks_generate_sixteen_lessons(
    client, lesson_seed: LessonSeed
) -> None:
    seed = lesson_seed
    headers = await login(client, seed.admin_phone)
    start = schedule_start()
    weekdays = two_weekdays(start)
    await create_rule(client, headers, seed, weekdays=weekdays, start=start)

    response = await generate(client, headers, seed)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["class_id"] == str(seed.class_id)
    assert body["generated"] == 16
    assert body["skipped_holidays"] == 0
    assert body["skipped_existing"] == 0
    assert body["from_date"] == start.isoformat()
    assert body["to_date"] == (start + timedelta(days=55)).isoformat()

    listing = await list_lessons(client, headers, seed)
    items = listing["items"]
    assert listing["total"] == 16
    assert {item["start_at"][:10] for item in items} == expected_dates(start, weekdays)
    # 时区必须是 aware 的 +08:00，时间取自规律。
    assert {item["start_at"][11:] for item in items} == {"18:00:00+08:00"}
    assert {item["end_at"][11:] for item in items} == {"19:30:00+08:00"}
    assert {item["status"] for item in items} == {"normal"}
    assert items[0]["class_name"] == "初二物理冲刺班"
    assert items[0]["teacher_name"] == "排课老师"
    assert items[0]["classroom_name"] == "多媒体教室"

    # 日期区间过滤：第一周只有两天。
    first_week = await list_lessons(
        client,
        headers,
        seed,
        **{"from": start.isoformat(), "to": (start + timedelta(days=6)).isoformat()},
    )
    assert first_week["total"] == 2


async def test_holidays_are_skipped(client, lesson_seed: LessonSeed) -> None:
    seed = lesson_seed
    headers = await login(client, seed.admin_phone)
    start = schedule_start()
    weekdays = two_weekdays(start)
    await create_rule(client, headers, seed, weekdays=weekdays, start=start)

    # 两个节假日都落在第一个 weekday 上（第 2、4 周）。
    first_holiday = start + timedelta(days=7)
    second_holiday = start + timedelta(days=21)
    assert first_holiday.weekday() == weekdays[0]
    assert second_holiday.weekday() == weekdays[0]

    holidays = await client.post(
        "/tenant-holidays",
        json={"dates": [first_holiday.isoformat(), second_holiday.isoformat()], "name": "机构假期"},
        headers=headers,
    )
    assert holidays.status_code == 200, holidays.text
    assert holidays.json()["created"] == 2

    response = await generate(client, headers, seed)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["generated"] == 14
    assert body["skipped_holidays"] == 2
    assert body["skipped_existing"] == 0

    listing = await list_lessons(client, headers, seed)
    lesson_dates = {item["start_at"][:10] for item in listing["items"]}
    assert listing["total"] == 14
    assert lesson_dates == expected_dates(start, weekdays) - {
        first_holiday.isoformat(),
        second_holiday.isoformat(),
    }


async def test_repeated_generation_is_idempotent(client, lesson_seed: LessonSeed) -> None:
    seed = lesson_seed
    headers = await login(client, seed.admin_phone)
    start = schedule_start()
    await create_rule(client, headers, seed, weekdays=two_weekdays(start), start=start)

    first = await generate(client, headers, seed)
    assert first.json()["generated"] == 16

    second = await generate(client, headers, seed)
    assert second.status_code == 200, second.text
    assert second.json()["generated"] == 0
    assert second.json()["skipped_existing"] == 16
    assert second.json()["skipped_holidays"] == 0

    assert (await list_lessons(client, headers, seed))["total"] == 16


async def test_generation_validates_request_and_preconditions(
    client, lesson_seed: LessonSeed
) -> None:
    seed = lesson_seed
    headers = await login(client, seed.admin_phone)
    start = schedule_start()
    await create_rule(client, headers, seed, weekdays=two_weekdays(start), start=start)

    for weeks in (0, 53):
        response = await generate(client, headers, seed, weeks=weeks)
        assert response.status_code == 422, (weeks, response.text)
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    missing = await generate(client, headers, seed, class_id=uuid4())
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "CLASS_NOT_FOUND"

    no_rule = await generate(client, headers, seed, class_id=seed.empty_class_id)
    assert no_rule.status_code == 409
    assert no_rule.json()["error"]["code"] == "NO_SCHEDULE_RULE"

    ended = await generate(client, headers, seed, class_id=seed.ended_class_id)
    assert ended.status_code == 409
    assert ended.json()["error"]["code"] == "CLASS_ENDED"

    # 全部被拒之后一条课次都没有落库。
    assert (await list_lessons(client, headers, seed))["total"] == 0


async def test_cross_tenant_lessons_are_invisible_and_teacher_is_denied(
    client, owner_engine, lesson_seed: LessonSeed
) -> None:
    seed = lesson_seed
    headers = await login(client, seed.admin_phone)
    start = schedule_start()
    await create_rule(client, headers, seed, weekdays=two_weekdays(start), start=start)
    assert (await generate(client, headers, seed)).json()["generated"] == 16

    other_tenant, foreign_teacher, foreign_room = uuid4(), uuid4(), uuid4()
    foreign_course, foreign_class, foreign_lesson = uuid4(), uuid4(), uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
            {"id": other_tenant, "name": f"别家机构-{uuid4().hex[:8]}"},
        )
        await connection.execute(
            text("INSERT INTO teacher (id, tenant_id, name) VALUES (:id, :tenant, '别家老师')"),
            {"id": foreign_teacher, "tenant": other_tenant},
        )
        await connection.execute(
            text(
                "INSERT INTO classroom (id, tenant_id, name, capacity) "
                "VALUES (:id, :tenant, '别家教室', 10)"
            ),
            {"id": foreign_room, "tenant": other_tenant},
        )
        await connection.execute(
            text("INSERT INTO course (id, tenant_id, name) VALUES (:id, :tenant, '别家课程')"),
            {"id": foreign_course, "tenant": other_tenant},
        )
        await connection.execute(
            text(
                "INSERT INTO class (id, tenant_id, course_id, teacher_id, classroom_id, name) "
                "VALUES (:id, :tenant, :course, :teacher, :room, '别家班级')"
            ),
            {
                "id": foreign_class,
                "tenant": other_tenant,
                "course": foreign_course,
                "teacher": foreign_teacher,
                "room": foreign_room,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO lesson "
                "(id, tenant_id, class_id, classroom_id, teacher_id, start_at, end_at, status) "
                "VALUES (:id, :tenant, :class, :room, :teacher, now(), now() + interval '1 hour',"
                " 'normal')"
            ),
            {
                "id": foreign_lesson,
                "tenant": other_tenant,
                "class": foreign_class,
                "room": foreign_room,
                "teacher": foreign_teacher,
            },
        )

    listing = await list_lessons(client, headers, seed)
    assert str(foreign_lesson) not in {item["id"] for item in listing["items"]}
    everything = await client.get("/lessons", headers=headers)
    assert str(foreign_lesson) not in {item["id"] for item in everything.json()["items"]}

    foreign = await generate(client, headers, seed, class_id=foreign_class)
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "CLASS_NOT_FOUND"

    teacher = await login(client, seed.teacher_phone)
    denied_generate = await generate(client, teacher, seed)
    assert denied_generate.status_code == 403
    assert denied_generate.json()["error"]["code"] == "OUT_OF_SCOPE"
    denied_read = await client.get("/lessons", headers=teacher)
    assert denied_read.status_code == 403
    assert denied_read.json()["error"]["code"] == "OUT_OF_SCOPE"
