"""Scheduling conflict detection and EXCLUDE backstop (step 3.3).

Dimensions covered: teacher, classroom, student cross-class, boundary-touching
non-overlap, cancelled non-occupancy, validate-without-writing, the generate
``skipped_conflicts`` counter, and — the soul test — two concurrent sessions
inserting overlapping lessons where exactly one wins.

Single-insert clash tests go through ``create_lesson`` under a real tenant
context (the same path the 3.4 single-add endpoint will reuse), so a clash
surfaces as ``AppError(409, SCHEDULE_CONFLICT)`` rather than an HTTP response.
"""

import asyncio
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from app.core.errors import AppError
from app.core.security import hash_password
from app.db.session import AsyncSessionFactory
from app.db.tenant import tenant_context
from app.services.ledger import BUSINESS_TZ, business_today
from app.services.lesson import create_lesson
from tests.auth_support import unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run conflict tests",
    ),
]

PASSWORD = "conflict-password"
BASE_DAY = date(2026, 11, 3)  # 周二；直接插入/冲突检测用的绝对时间锚点


@dataclass(frozen=True, slots=True)
class ConflictSeed:
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
async def conflict_seed(owner_engine) -> ConflictSeed:
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
            {"id": tenant_id, "name": f"冲突机构-{uuid4().hex[:8]}"},
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
        # student1 同时在 A、B 两班 → 学员跨班冲突的原料；student2 只在 B 班。
        for class_id, student_id in (
            (class_a, student1),
            (class_b, student1),
            (class_b, student2),
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
                "INSERT INTO staff_account "
                "(id, tenant_id, name, phone, password_hash, role, status) "
                "VALUES (:id, :t, '管理员', :phone, :hash, 'admin', 'active')"
            ),
            {
                "id": uuid4(),
                "t": tenant_id,
                "phone": admin_phone,
                "hash": password_hash,
            },
        )

    return ConflictSeed(
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


def slot(sh: int, sm: int, eh: int, em: int) -> tuple[datetime, datetime]:
    """绝对时间对，锚在 BASE_DAY。"""

    return (
        datetime.combine(BASE_DAY, time(sh, sm), tzinfo=BUSINESS_TZ),
        datetime.combine(BASE_DAY, time(eh, em), tzinfo=BUSINESS_TZ),
    )


async def seed_lesson(
    owner_engine,
    seed: ConflictSeed,
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


async def try_create(seed: ConflictSeed, *, class_id, classroom_id, teacher_id, start_at, end_at):
    """在真实租户上下文里插入一条课次；冲突时抛出 AppError(409)。"""

    async with AsyncSessionFactory() as session:
        async with tenant_context(session, seed.tenant_id):
            return await create_lesson(
                session,
                tenant_id=seed.tenant_id,
                class_id=class_id,
                classroom_id=classroom_id,
                teacher_id=teacher_id,
                start_at=start_at,
                end_at=end_at,
            )


# ----------------------------------------------------- single-insert clashes


async def test_teacher_conflict_rejected(owner_engine, conflict_seed: ConflictSeed) -> None:
    seed = conflict_seed
    start, end = slot(18, 0, 19, 0)
    await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=start,
        end_at=end,
    )
    clash_start, clash_end = slot(18, 30, 19, 30)
    with pytest.raises(AppError) as exc:
        await try_create(
            seed,
            class_id=seed.class_b_id,
            classroom_id=seed.classroom2_id,
            teacher_id=seed.teacher1_id,
            start_at=clash_start,
            end_at=clash_end,
        )
    assert exc.value.status_code == 409
    assert exc.value.code == "SCHEDULE_CONFLICT"
    assert exc.value.detail["conflicts"][0]["type"] == "teacher"


async def test_classroom_conflict_rejected(owner_engine, conflict_seed: ConflictSeed) -> None:
    seed = conflict_seed
    start, end = slot(18, 0, 19, 0)
    await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=start,
        end_at=end,
    )
    clash_start, clash_end = slot(18, 30, 19, 30)
    with pytest.raises(AppError) as exc:
        await try_create(
            seed,
            class_id=seed.class_b_id,
            classroom_id=seed.classroom1_id,
            teacher_id=seed.teacher2_id,
            start_at=clash_start,
            end_at=clash_end,
        )
    assert exc.value.status_code == 409
    kinds = {c["type"] for c in exc.value.detail["conflicts"]}
    assert "classroom" in kinds


async def test_student_cross_class_conflict(owner_engine, conflict_seed: ConflictSeed) -> None:
    seed = conflict_seed
    start, end = slot(18, 0, 19, 0)
    await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=start,
        end_at=end,
    )
    clash_start, clash_end = slot(18, 30, 19, 30)
    # teacher2 + classroom2 都与 A 班已有课不同，唯一重叠是学员 student1 跨班。
    with pytest.raises(AppError) as exc:
        await try_create(
            seed,
            class_id=seed.class_b_id,
            classroom_id=seed.classroom2_id,
            teacher_id=seed.teacher2_id,
            start_at=clash_start,
            end_at=clash_end,
        )
    assert exc.value.status_code == 409
    kinds = {c["type"] for c in exc.value.detail["conflicts"]}
    assert "student" in kinds


async def test_boundary_touching_does_not_conflict(
    owner_engine, conflict_seed: ConflictSeed
) -> None:
    seed = conflict_seed
    start, end = slot(18, 0, 19, 0)
    await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=start,
        end_at=end,
    )
    # 同老师同教室，但 19:00 结束 == 19:00 开始，边界相接不算重叠。
    next_start, next_end = slot(19, 0, 20, 0)
    lesson = await try_create(
        seed,
        class_id=seed.class_b_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=next_start,
        end_at=next_end,
    )
    assert lesson.status == "normal"


async def test_cancelled_lesson_does_not_occupy(owner_engine, conflict_seed: ConflictSeed) -> None:
    seed = conflict_seed
    start, end = slot(18, 0, 19, 0)
    await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=start,
        end_at=end,
        status="cancelled",
    )
    clash_start, clash_end = slot(18, 30, 19, 30)
    lesson = await try_create(
        seed,
        class_id=seed.class_b_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=clash_start,
        end_at=clash_end,
    )
    assert lesson.status == "normal"


# ------------------------------------------------------------- concurrency


async def test_concurrent_insert_exactly_one_wins(conflict_seed: ConflictSeed) -> None:
    seed = conflict_seed
    start, end = slot(18, 0, 19, 0)
    results = await asyncio.gather(
        try_create(
            seed,
            class_id=seed.class_a_id,
            classroom_id=seed.classroom1_id,
            teacher_id=seed.teacher1_id,
            start_at=start,
            end_at=end,
        ),
        try_create(
            seed,
            class_id=seed.class_b_id,
            classroom_id=seed.classroom1_id,
            teacher_id=seed.teacher1_id,
            start_at=start,
            end_at=end,
        ),
        return_exceptions=True,
    )
    ok = [r for r in results if not isinstance(r, BaseException)]
    errors = [r for r in results if isinstance(r, AppError)]
    assert len(ok) == 1, results
    assert len(errors) == 1, results
    assert errors[0].status_code == 409
    assert errors[0].code == "SCHEDULE_CONFLICT"


# --------------------------------------------------- validate & generation


async def test_validate_reports_conflicts_without_writing(
    client, owner_engine, conflict_seed: ConflictSeed
) -> None:
    seed = conflict_seed
    start_day = business_today() + timedelta(days=1)
    clash_start = datetime.combine(start_day, time(18, 0), tzinfo=BUSINESS_TZ)
    clash_end = datetime.combine(start_day, time(19, 0), tzinfo=BUSINESS_TZ)
    await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=clash_start,
        end_at=clash_end,
    )
    headers = await login(client, seed.admin_phone)

    response = await client.post(
        "/schedule-rules/validate",
        json={
            "class_id": str(seed.class_b_id),
            "classroom_id": str(seed.classroom2_id),
            "teacher_id": str(seed.teacher1_id),  # 与 A 班已有课同老师 → 冲突
            "weekdays": [start_day.weekday()],
            "start_time": "18:00:00",
            "end_time": "19:00:00",
            "start_date": start_day.isoformat(),
            "weeks": 1,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    kinds = {c["type"] for c in body["conflicts"]}
    assert "teacher" in kinds
    assert all("+08:00" in c["start_at"] for c in body["conflicts"])

    async with owner_engine.connect() as connection:
        count = await connection.execute(
            text("SELECT count(*) FROM lesson WHERE class_id = :id"), {"id": seed.class_b_id}
        )
        assert count.scalar_one() == 0


async def test_generate_skips_conflicts(client, owner_engine, conflict_seed: ConflictSeed) -> None:
    seed = conflict_seed
    start_day = business_today() + timedelta(days=1)
    clash_start = datetime.combine(start_day, time(18, 0), tzinfo=BUSINESS_TZ)
    clash_end = datetime.combine(start_day, time(19, 0), tzinfo=BUSINESS_TZ)
    await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=clash_start,
        end_at=clash_end,
    )
    headers = await login(client, seed.admin_phone)

    rule = await client.post(
        "/schedule-rules",
        json={
            "class_id": str(seed.class_b_id),
            "classroom_id": str(seed.classroom1_id),
            "teacher_id": str(seed.teacher1_id),
            "weekdays": [start_day.weekday()],
            "start_time": "18:00:00",
            "end_time": "19:00:00",
            "start_date": start_day.isoformat(),
            "weeks": 1,
        },
        headers=headers,
    )
    assert rule.status_code == 201, rule.text

    response = await client.post(
        "/lessons/generate", json={"class_id": str(seed.class_b_id), "weeks": 1}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["generated"] == 0
    assert body["skipped_conflicts"] == 1
    assert body["skipped_existing"] == 0
    assert body["skipped_holidays"] == 0
    assert len(body["conflicts"]) >= 1
