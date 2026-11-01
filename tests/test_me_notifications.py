"""站内信 / 红点兜底（step 5.5）—— 阶段 5 收官。

What must never break:

* 一次签到扣课 → 该学生的**每个**绑定家长各一条消息，余额是扣后余额；
* 重复签到（幂等命中）→ 零新增消息；唯一键直插重复也不炸；
* 站内信插入失败**不许**把签到顶回去（SAVEPOINT 放行）；
* 读侧只认 token 身份链：跨家长 / 跨租户的 id 一律 404，老师 403，未登录 401；
* 列表与红点同源：``unread`` 不靠前端再算一遍。
"""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from app.core.security import hash_password
from app.services.ledger import BUSINESS_TZ
from tests.auth_support import seed_subscription, unique_openid, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run notification tests",
    ),
]

PASSWORD = "notify-password"


@dataclass(frozen=True, slots=True)
class NotifySeed:
    tenant_id: UUID
    other_tenant_id: UUID
    course_id: UUID
    lesson_id: UUID
    student_id: UUID
    other_student_id: UUID
    admin_phone: str
    teacher_phone: str
    parent1_openid: str
    parent2_openid: str
    other_parent_openid: str


async def login_staff(client: httpx.AsyncClient, phone: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"phone": phone, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def login_parent(client: httpx.AsyncClient, openid: str) -> dict[str, str]:
    response = await client.post("/auth/wechat-login", json={"wx_code": openid})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def add_parent(engine, *, tenant_id: UUID, student_id: UUID, openid: str) -> UUID:
    """一个微信身份 + 一个 parent 行 + 一条绑定，返回 parent_id。"""

    wechat_user_id, parent_id = uuid4(), uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO wechat_user (id) VALUES (:id)"), {"id": wechat_user_id}
        )
        await connection.execute(
            text(
                "INSERT INTO wechat_openid (id, wechat_user_id, app_id, openid) "
                "VALUES (:id, :u, 'wx_platform_stub', :openid)"
            ),
            {"id": uuid4(), "u": wechat_user_id, "openid": openid},
        )
        await connection.execute(
            text(
                "INSERT INTO parent (id, tenant_id, name, phone, wechat_user_id) "
                "VALUES (:id, :t, '家长', :phone, :u)"
            ),
            {"id": parent_id, "t": tenant_id, "phone": unique_phone(), "u": wechat_user_id},
        )
        await connection.execute(
            text(
                "INSERT INTO parent_student (id, tenant_id, parent_id, student_id, relation) "
                "VALUES (:id, :t, :p, :s, '妈妈')"
            ),
            {"id": uuid4(), "t": tenant_id, "p": parent_id, "s": student_id},
        )
    return parent_id


@pytest.fixture
async def notify_seed(owner_engine) -> NotifySeed:
    """一个机构：1 节课、1 个学生、2 个绑定家长；另一个机构当跨租户靶子。"""

    tenant_id, other_tenant_id = uuid4(), uuid4()
    course_id, class_id, classroom_id, teacher_id = uuid4(), uuid4(), uuid4(), uuid4()
    student_id, other_student_id, lesson_id = uuid4(), uuid4(), uuid4()
    admin_phone, teacher_phone = unique_phone(), unique_phone()

    started = datetime.now(BUSINESS_TZ) - timedelta(hours=2)
    async with owner_engine.begin() as connection:
        for tid, name in ((tenant_id, "站内信机构"), (other_tenant_id, "别家机构")):
            await connection.execute(
                text("INSERT INTO tenant (id, name) VALUES (:id, :name)"), {"id": tid, "name": name}
            )
            await seed_subscription(connection, tid)
        await connection.execute(
            text("INSERT INTO teacher (id, tenant_id, name) VALUES (:id, :t, '老师')"),
            {"id": teacher_id, "t": tenant_id},
        )
        await connection.execute(
            text(
                "INSERT INTO staff_account "
                "(id, tenant_id, name, phone, password_hash, role, status) "
                "VALUES (:id, :t, '管理员', :p, :h, 'admin', 'active')"
            ),
            {"id": uuid4(), "t": tenant_id, "p": admin_phone, "h": hash_password(PASSWORD)},
        )
        await connection.execute(
            text(
                "INSERT INTO staff_account "
                "(id, tenant_id, name, phone, password_hash, role, teacher_id, status) "
                "VALUES (:id, :t, '老师', :p, :h, 'teacher', :tid, 'active')"
            ),
            {
                "id": uuid4(),
                "t": tenant_id,
                "p": teacher_phone,
                "h": hash_password(PASSWORD),
                "tid": teacher_id,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO classroom (id, tenant_id, name, capacity) VALUES (:id, :t, '教室', 20)"
            ),
            {"id": classroom_id, "t": tenant_id},
        )
        await connection.execute(
            text("INSERT INTO course (id, tenant_id, name) VALUES (:id, :t, '物理')"),
            {"id": course_id, "t": tenant_id},
        )
        await connection.execute(
            text(
                "INSERT INTO class "
                "(id, tenant_id, course_id, teacher_id, classroom_id, name) "
                "VALUES (:id, :t, :c, :th, :r, 'A班')"
            ),
            {
                "id": class_id,
                "t": tenant_id,
                "c": course_id,
                "th": teacher_id,
                "r": classroom_id,
            },
        )
        for sid, name in ((student_id, "小明"), (other_student_id, "小红")):
            await connection.execute(
                text("INSERT INTO student (id, tenant_id, name) VALUES (:id, :t, :name)"),
                {"id": sid, "t": tenant_id if sid == student_id else other_tenant_id, "name": name},
            )
        await connection.execute(
            text(
                "INSERT INTO lesson "
                "(id, tenant_id, class_id, classroom_id, teacher_id, start_at, end_at) "
                "VALUES (:id, :t, :c, :r, :th, :s, :e)"
            ),
            {
                "id": lesson_id,
                "t": tenant_id,
                "c": class_id,
                "r": classroom_id,
                "th": teacher_id,
                "s": started,
                "e": started + timedelta(hours=1),
            },
        )

    parent1_openid = unique_openid("notify-parent1")
    parent2_openid = unique_openid("notify-parent2")
    other_parent_openid = unique_openid("notify-other")
    await add_parent(
        owner_engine, tenant_id=tenant_id, student_id=student_id, openid=parent1_openid
    )
    await add_parent(
        owner_engine, tenant_id=tenant_id, student_id=student_id, openid=parent2_openid
    )
    await add_parent(
        owner_engine,
        tenant_id=other_tenant_id,
        student_id=other_student_id,
        openid=other_parent_openid,
    )

    return NotifySeed(
        tenant_id=tenant_id,
        other_tenant_id=other_tenant_id,
        course_id=course_id,
        lesson_id=lesson_id,
        student_id=student_id,
        other_student_id=other_student_id,
        admin_phone=admin_phone,
        teacher_phone=teacher_phone,
        parent1_openid=parent1_openid,
        parent2_openid=parent2_openid,
        other_parent_openid=other_parent_openid,
    )


async def buy(client, headers, seed: NotifySeed, *, lesson_count: int = 5) -> None:
    response = await client.post(
        "/purchases",
        json={
            "student_id": str(seed.student_id),
            "course_id": str(seed.course_id),
            "lesson_count": lesson_count,
            "valid_months": 6,
            "amount": "1000.00",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text


async def mark(
    client, headers, seed: NotifySeed, *, student_id: UUID | None = None
) -> httpx.Response:
    return await client.post(
        f"/lessons/{seed.lesson_id}/attendance",
        json={
            "records": [
                {"student_id": str(student_id or seed.student_id), "status": "present"},
            ]
        },
        headers=headers,
    )


async def count_messages(
    owner_engine, *, parent_id: UUID | None = None, tenant_id: UUID | None = None
):
    sql = "SELECT count(*) FROM in_app_message WHERE 1=1"
    params: dict = {}
    if parent_id is not None:
        sql += " AND parent_id = :p"
        params["p"] = parent_id
    if tenant_id is not None:
        sql += " AND tenant_id = :t"
        params["t"] = tenant_id
    async with owner_engine.connect() as connection:
        return int(await connection.scalar(text(sql), params))


async def parent_of(owner_engine, *, tenant_id: UUID, student_id: UUID, openid: str) -> UUID:
    async with owner_engine.connect() as connection:
        return await connection.scalar(
            text(
                "SELECT p.id FROM parent p "
                "JOIN wechat_openid o ON o.wechat_user_id = p.wechat_user_id "
                "JOIN parent_student ps ON ps.parent_id = p.id "
                "WHERE p.tenant_id = :t AND ps.student_id = :s AND o.openid = :o"
            ),
            {"t": tenant_id, "s": student_id, "o": openid},
        )


# ------------------------------------------------------------------ 写侧


async def test_check_in_writes_one_message_per_parent(client, owner_engine, notify_seed) -> None:
    seed = notify_seed
    headers = await login_staff(client, seed.admin_phone)
    await buy(client, headers, seed, lesson_count=5)

    response = await mark(client, headers, seed)
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["deduct_lessons"] == 1

    parent1 = await parent_of(
        owner_engine,
        tenant_id=seed.tenant_id,
        student_id=seed.student_id,
        openid=seed.parent1_openid,
    )
    parent2 = await parent_of(
        owner_engine,
        tenant_id=seed.tenant_id,
        student_id=seed.student_id,
        openid=seed.parent2_openid,
    )
    assert await count_messages(owner_engine, parent_id=parent1) == 1
    assert await count_messages(owner_engine, parent_id=parent2) == 1

    parent1_headers = await login_parent(client, seed.parent1_openid)
    listed = await client.get("/me/notifications", headers=parent1_headers)
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert payload["total"] == 1 and payload["unread"] == 1
    item = payload["items"][0]
    assert item["title"] == "小明 已签到扣课"
    # 5 课时扣 1 → 剩 4；余额必须是扣后的数，不是扣前。
    assert item["body"] == "本节课已扣 1 课时，剩余 4 课时"
    assert item["read"] is False
    assert item["type"] == "lesson_deduct"


async def test_repeat_check_in_writes_no_second_message(client, owner_engine, notify_seed) -> None:
    seed = notify_seed
    headers = await login_staff(client, seed.admin_phone)
    await buy(client, headers, seed, lesson_count=5)

    await mark(client, headers, seed)
    again = await mark(client, headers, seed)
    assert again.status_code == 200, again.text
    assert again.json()["results"][0]["deduplicated"] is True

    assert await count_messages(owner_engine, tenant_id=seed.tenant_id) == 2  # 两个家长，各一条


async def test_duplicate_source_row_is_swallowed(owner_engine, notify_seed) -> None:
    """唯一键直插重复不炸：同一次签到再写一遍，家长还是只有一条。"""

    from app.db.session import AsyncSessionFactory
    from app.db.tenant import tenant_context
    from app.services.notification import record_lesson_deduct

    seed = notify_seed
    parent_id = await parent_of(
        owner_engine,
        tenant_id=seed.tenant_id,
        student_id=seed.student_id,
        openid=seed.parent1_openid,
    )
    attendance_id = uuid4()

    async with AsyncSessionFactory() as session:
        async with tenant_context(session, seed.tenant_id):
            for _ in range(2):
                await record_lesson_deduct(
                    session,
                    tenant_id=seed.tenant_id,
                    student_id=seed.student_id,
                    attendance_id=attendance_id,
                    count=1,
                )
            await session.commit()

    assert await count_messages(owner_engine, parent_id=parent_id) == 1


async def test_message_failure_does_not_block_check_in(
    client, owner_engine, notify_seed, monkeypatch
) -> None:
    """消息表炸了，签到也得成：这是产品红线（家长的课不能因为消息上不了）。"""

    seed = notify_seed
    headers = await login_staff(client, seed.admin_phone)
    await buy(client, headers, seed, lesson_count=5)

    import app.services.attendance as attendance_service

    def boom(*args, **kwargs):
        raise RuntimeError("in_app_message is down")

    monkeypatch.setattr(attendance_service, "record_lesson_deduct", boom)

    response = await mark(client, headers, seed)
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["deduplicated"] is False

    async with owner_engine.connect() as connection:
        seats = await connection.scalar(
            text("SELECT count(*) FROM attendance WHERE lesson_id = :l"), {"l": seed.lesson_id}
        )
        ledger = await connection.scalar(
            text("SELECT count(*) FROM lesson_ledger WHERE student_id = :s AND type = 'consume'"),
            {"s": seed.student_id},
        )
    assert seats == 1
    assert ledger == 1
    assert await count_messages(owner_engine, tenant_id=seed.tenant_id) == 0


# ------------------------------------------------------------------ 读侧


async def test_list_pagination_and_unread_count(client, owner_engine, notify_seed) -> None:
    seed = notify_seed
    headers = await login_staff(client, seed.admin_phone)
    await buy(client, headers, seed, lesson_count=10)

    # 造 3 条互不相同的消息：直插（source_id 不同），不依赖签到次数。
    parent_id = await parent_of(
        owner_engine,
        tenant_id=seed.tenant_id,
        student_id=seed.student_id,
        openid=seed.parent1_openid,
    )
    async with owner_engine.begin() as connection:
        for index in range(3):
            await connection.execute(
                text(
                    "INSERT INTO in_app_message (id, tenant_id, parent_id, student_id, "
                    "type, title, body, source_type, source_id) "
                    "VALUES (:id, :t, :p, :s, 'lesson_deduct', :title, '正文', 'attendance', :src)"
                ),
                {
                    "id": uuid4(),
                    "t": seed.tenant_id,
                    "p": parent_id,
                    "s": seed.student_id,
                    "title": f"第 {index} 条",
                    "src": uuid4(),
                },
            )

    parent_headers = await login_parent(client, seed.parent1_openid)
    first = await client.get(
        "/me/notifications", params={"page": 1, "page_size": 2}, headers=parent_headers
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["total"] == 3 and body["unread"] == 3 and len(body["items"]) == 2

    second = await client.get(
        "/me/notifications", params={"page": 2, "page_size": 2}, headers=parent_headers
    )
    assert len(second.json()["items"]) == 1

    unread = await client.get("/me/notifications/unread-count", headers=parent_headers)
    assert unread.status_code == 200 and unread.json() == {"unread": 3}

    # page_size 上限 50
    too_big = await client.get(
        "/me/notifications", params={"page_size": 51}, headers=parent_headers
    )
    assert too_big.status_code == 422


async def test_mark_single_and_all_read(client, owner_engine, notify_seed) -> None:
    seed = notify_seed
    headers = await login_staff(client, seed.admin_phone)
    await buy(client, headers, seed, lesson_count=10)
    await mark(client, headers, seed)

    parent_headers = await login_parent(client, seed.parent1_openid)
    listed = (await client.get("/me/notifications", headers=parent_headers)).json()
    message_id = listed["items"][0]["id"]

    single = await client.post(
        "/me/notifications/read", json={"ids": [message_id]}, headers=parent_headers
    )
    assert single.status_code == 200 and single.json() == {"marked": 1}

    # 同一条再标一次：已读不再计数（也不是错误）
    again = await client.post(
        "/me/notifications/read", json={"ids": [message_id]}, headers=parent_headers
    )
    assert again.json() == {"marked": 0}

    assert (await client.get("/me/notifications/unread-count", headers=parent_headers)).json() == {
        "unread": 0
    }


async def test_mark_all_read_with_empty_ids(client, owner_engine, notify_seed) -> None:
    seed = notify_seed
    headers = await login_staff(client, seed.admin_phone)
    await buy(client, headers, seed, lesson_count=10)
    await mark(client, headers, seed)  # 两条：parent1 / parent2 各一条

    parent_headers = await login_parent(client, seed.parent1_openid)
    all_read = await client.post("/me/notifications/read", json={"ids": []}, headers=parent_headers)
    assert all_read.status_code == 200 and all_read.json() == {"marked": 1}
    assert (await client.get("/me/notifications/unread-count", headers=parent_headers)).json() == {
        "unread": 0
    }


async def test_cross_parent_message_is_404(client, owner_engine, notify_seed) -> None:
    seed = notify_seed
    headers = await login_staff(client, seed.admin_phone)
    await buy(client, headers, seed, lesson_count=5)
    await mark(client, headers, seed)

    parent2_headers = await login_parent(client, seed.parent2_openid)
    parent2_items = (await client.get("/me/notifications", headers=parent2_headers)).json()["items"]
    assert len(parent2_items) == 1

    parent1_headers = await login_parent(client, seed.parent1_openid)
    stolen = await client.post(
        "/me/notifications/read", json={"ids": [parent2_items[0]["id"]]}, headers=parent1_headers
    )
    assert stolen.status_code == 404, stolen.text
    assert stolen.json()["error"]["code"] == "MESSAGE_NOT_FOUND"

    # 家长 2 的那条没被顺带标已读
    assert (await client.get("/me/notifications/unread-count", headers=parent2_headers)).json() == {
        "unread": 1
    }


async def test_cross_tenant_isolation(client, owner_engine, notify_seed) -> None:
    seed = notify_seed
    headers = await login_staff(client, seed.admin_phone)
    await buy(client, headers, seed, lesson_count=5)
    await mark(client, headers, seed)

    other_headers = await login_parent(client, seed.other_parent_openid)
    listed = await client.get("/me/notifications", headers=other_headers)
    assert listed.status_code == 200
    assert listed.json() == {"items": [], "total": 0, "unread": 0}

    tenant_one_item = (
        await client.get(
            "/me/notifications", headers=await login_parent(client, seed.parent1_openid)
        )
    ).json()["items"][0]
    forbidden = await client.post(
        "/me/notifications/read", json={"ids": [tenant_one_item["id"]]}, headers=other_headers
    )
    assert forbidden.status_code == 404, forbidden.text


async def test_teacher_and_anonymous_are_rejected(client, notify_seed) -> None:
    seed = notify_seed
    teacher_headers = await login_staff(client, seed.teacher_phone)

    for path in ("/me/notifications", "/me/notifications/unread-count"):
        rejected = await client.get(path, headers=teacher_headers)
        assert rejected.status_code == 403, path
        assert rejected.json()["error"]["code"] == "OUT_OF_SCOPE"

    anonymous = await client.get("/me/notifications")
    assert anonymous.status_code == 401
