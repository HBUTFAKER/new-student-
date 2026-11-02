"""Lesson feedback (048): teacher writes post-class notes, parents read them.

What must never break:

* 老师写 → 家长能查到；覆盖更新（PUT 已存在）→ 家长看到新版；
* 老师只能写自己任教课次，写别人的课次 404（048 口径，非签到那套 403）；
* 教务 / admin 可代写任意课次；
* 家长只能查自己绑定的孩子，非绑定孩子 404；
* 重复 PUT 幂等（唯一约束 ``(lesson_id, student_id)`` upsert，不炸 500）；
* 反馈不触发通知 / 不进通知引擎（本测试不验证通知，因为写路径根本不调它）。
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
from tests.auth_support import seed_subscription, unique_openid, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run feedback tests",
    ),
]

PASSWORD = "feedback-password"


@dataclass(frozen=True, slots=True)
class FeedbackSeed:
    tenant_id: UUID
    admin_id: UUID
    admin_phone: str
    teacher1_phone: str
    teacher2_phone: str
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
    parent_openid: str


async def login(client: httpx.AsyncClient, phone: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"phone": phone, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def parent_headers(client: httpx.AsyncClient, openid: str) -> dict[str, str]:
    response = await client.post("/auth/wechat-login", json={"wx_code": openid})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def feedback_seed(owner_engine) -> FeedbackSeed:
    tenant_id = uuid4()
    admin_id = uuid4()
    teacher1, teacher2 = uuid4(), uuid4()
    classroom1, classroom2 = uuid4(), uuid4()
    course_id = uuid4()
    class_a, class_b = uuid4(), uuid4()
    student1, student2, student3 = uuid4(), uuid4(), uuid4()
    wechat_user_id, parent_id = uuid4(), uuid4()
    admin_phone, teacher1_phone, teacher2_phone = unique_phone(), unique_phone(), unique_phone()
    parent_openid = unique_openid("feedback-parent")
    password_hash = hash_password(PASSWORD)

    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
            {"id": tenant_id, "name": f"反馈机构-{uuid4().hex[:8]}"},
        )
        await seed_subscription(connection, tenant_id)
        for teacher_id in (teacher1, teacher2):
            name = "王老师" if teacher_id == teacher1 else "李老师"
            await connection.execute(
                text("INSERT INTO teacher (id, tenant_id, name) VALUES (:id, :t, :n)"),
                {"id": teacher_id, "t": tenant_id, "n": name},
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
            text("INSERT INTO course (id, tenant_id, name) VALUES (:id, :t, :n)"),
            {"id": course_id, "t": tenant_id, "n": "少儿编程"},
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
            (class_b, student3),
        ):
            await connection.execute(
                text(
                    "INSERT INTO class_enrollment (id, tenant_id, class_id, student_id, joined_at) "
                    "VALUES (:id, :t, :c, :s, '2026-09-01')"
                ),
                {"id": uuid4(), "t": tenant_id, "c": class_id, "s": student_id},
            )
        for staff_id, phone, role, teacher_id in (
            (admin_id, admin_phone, "admin", None),
            (uuid4(), teacher1_phone, "teacher", teacher1),
            (uuid4(), teacher2_phone, "teacher", teacher2),
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
        # 家长身份 + 绑定 student1（查 student1 应 200，查 student2 应 404）。
        await connection.execute(
            text("INSERT INTO wechat_user (id) VALUES (:id)"),
            {"id": wechat_user_id},
        )
        await connection.execute(
            text(
                "INSERT INTO wechat_openid (id, wechat_user_id, app_id, openid) "
                "VALUES (:id, :u, 'wx_platform_stub', :openid)"
            ),
            {"id": uuid4(), "u": wechat_user_id, "openid": parent_openid},
        )
        await connection.execute(
            text(
                "INSERT INTO parent (id, tenant_id, name, phone, wechat_user_id) "
                "VALUES (:id, :t, '家长', '13800000000', :u)"
            ),
            {"id": parent_id, "t": tenant_id, "u": wechat_user_id},
        )
        await connection.execute(
            text(
                "INSERT INTO parent_student (id, tenant_id, parent_id, student_id, relation) "
                "VALUES (:id, :t, :p, :s, '家长')"
            ),
            {"id": uuid4(), "t": tenant_id, "p": parent_id, "s": student1},
        )

    return FeedbackSeed(
        tenant_id=tenant_id,
        admin_id=admin_id,
        admin_phone=admin_phone,
        teacher1_phone=teacher1_phone,
        teacher2_phone=teacher2_phone,
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
        parent_openid=parent_openid,
    )


def at(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=BUSINESS_TZ)


def past_day() -> date:
    return business_today() - timedelta(days=1)


async def seed_lesson(
    owner_engine,
    seed: FeedbackSeed,
    *,
    class_id,
    classroom_id,
    teacher_id,
    start_at,
    end_at,
) -> UUID:
    lesson_id = uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO lesson "
                "(id, tenant_id, class_id, classroom_id, teacher_id, start_at, end_at, status) "
                "VALUES (:id, :t, :c, :r, :th, :s, :e, 'normal')"
            ),
            {
                "id": lesson_id,
                "t": seed.tenant_id,
                "c": class_id,
                "r": classroom_id,
                "th": teacher_id,
                "s": start_at,
                "e": end_at,
            },
        )
    return lesson_id


async def put_feedback(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    lesson_id: UUID,
    items: list[tuple[UUID, str]],
) -> httpx.Response:
    return await client.put(
        f"/lessons/{lesson_id}/feedback",
        json={
            "items": [
                {"student_id": str(student_id), "content": content} for student_id, content in items
            ]
        },
        headers=headers,
    )


# ------------------------------------------------------- 老师写 → 家长查 + 覆盖更新


async def test_teacher_writes_parent_reads_and_update(
    client, owner_engine, feedback_seed: FeedbackSeed
) -> None:
    seed = feedback_seed
    teacher = await login(client, seed.teacher1_phone)
    lesson_id = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=at(past_day(), 18),
        end_at=at(past_day(), 19),
    )

    written = await put_feedback(client, teacher, lesson_id, [(seed.student1_id, "今天很认真")])
    assert written.status_code == 200, written.text
    assert written.json()["total"] == 1
    assert written.json()["items"][0]["content"] == "今天很认真"
    assert written.json()["items"][0]["student_name"] == "学生"
    assert written.json()["items"][0]["teacher_id"] == str(seed.teacher1_id)

    parent = await parent_headers(client, seed.parent_openid)
    listed = await client.get(f"/me/children/{seed.student1_id}/feedback", headers=parent)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["content"] == "今天很认真"
    assert item["class_name"] == "A班"
    assert item["course_name"] == "少儿编程"
    assert item["teacher_name"] == "王老师"

    # 覆盖更新：家长看到新版，且仍只有一条。
    updated = await put_feedback(client, teacher, lesson_id, [(seed.student1_id, "今天进步很大")])
    assert updated.status_code == 200, updated.text
    assert updated.json()["total"] == 1
    assert updated.json()["items"][0]["content"] == "今天进步很大"

    again = await client.get(f"/me/children/{seed.student1_id}/feedback", headers=parent)
    assert again.status_code == 200, again.text
    assert again.json()["total"] == 1
    assert again.json()["items"][0]["content"] == "今天进步很大"


# ------------------------------------------------------- 老师越界 404 / 教务代写


async def test_teacher_cannot_write_others_lesson_registrar_can(
    client, owner_engine, feedback_seed: FeedbackSeed
) -> None:
    seed = feedback_seed
    lesson_b = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_b_id,
        classroom_id=seed.classroom2_id,
        teacher_id=seed.teacher2_id,
        start_at=at(past_day(), 18),
        end_at=at(past_day(), 19),
    )

    # 老师1 写老师2 任教的课次 → 404（048 口径，非 403）。
    teacher1 = await login(client, seed.teacher1_phone)
    denied = await put_feedback(client, teacher1, lesson_b, [(seed.student3_id, "越界")])
    assert denied.status_code == 404, denied.text
    assert denied.json()["error"]["code"] == "LESSON_NOT_FOUND"

    # 老师2 写自己任教课次 → 200。
    teacher2 = await login(client, seed.teacher2_phone)
    own = await put_feedback(client, teacher2, lesson_b, [(seed.student3_id, "自己班")])
    assert own.status_code == 200, own.text

    # admin 代写任意课次 → 200，且 teacher_id 归任课老师。
    admin = await login(client, seed.admin_phone)
    proxy = await put_feedback(client, admin, lesson_b, [(seed.student3_id, "教务代写")])
    assert proxy.status_code == 200, proxy.text
    assert proxy.json()["items"][0]["teacher_id"] == str(seed.teacher2_id)


# ------------------------------------------------------- 家长越界 404


async def test_parent_cannot_read_unbound_child(
    client, owner_engine, feedback_seed: FeedbackSeed
) -> None:
    seed = feedback_seed
    lesson_id = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=at(past_day(), 18),
        end_at=at(past_day(), 19),
    )
    admin = await login(client, seed.admin_phone)
    await put_feedback(
        client, admin, lesson_id, [(seed.student1_id, "绑定"), (seed.student2_id, "未绑定")]
    )

    parent = await parent_headers(client, seed.parent_openid)
    # 绑定的 student1 → 200；同租户未绑定的 student2 → 404。
    own = await client.get(f"/me/children/{seed.student1_id}/feedback", headers=parent)
    assert own.status_code == 200, own.text
    assert own.json()["total"] == 1

    sibling = await client.get(f"/me/children/{seed.student2_id}/feedback", headers=parent)
    assert sibling.status_code == 404, sibling.text
    assert sibling.json()["error"]["code"] == "STUDENT_NOT_FOUND"


# ------------------------------------------------------- 重复 PUT 幂等


async def test_repeated_put_is_idempotent(
    client, owner_engine, feedback_seed: FeedbackSeed
) -> None:
    seed = feedback_seed
    admin = await login(client, seed.admin_phone)
    lesson_id = await seed_lesson(
        owner_engine,
        seed,
        class_id=seed.class_a_id,
        classroom_id=seed.classroom1_id,
        teacher_id=seed.teacher1_id,
        start_at=at(past_day(), 18),
        end_at=at(past_day(), 19),
    )

    for _ in range(5):
        resp = await put_feedback(client, admin, lesson_id, [(seed.student1_id, "幂等")])
        assert resp.status_code == 200, resp.text
        assert resp.json()["total"] == 1

    listed = await client.get(f"/lessons/{lesson_id}/feedback", headers=admin)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["content"] == "幂等"
