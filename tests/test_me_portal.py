"""家长端只读三件套（step 5.3 / 05 §9.3–9.5）。

覆盖：未选孩子 403、跨孩子不可见、cancelled 不出现、moved/makeup 徽章、31 天窗口、
无时区 422、余额与 admin 视角一致、过期包不进列表但计入 total、负 total、
分页上下限、type 筛选、teacher/admin 访问 /me/* 403、解绑后立即失效。

数据全部按绝对时间锚点造（``DAY``），不依赖 ``business_today()``，跑哪天都一样。
"""

import dataclasses
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from app.core.security import hash_password
from app.services.ledger import BUSINESS_TZ, business_today
from tests.auth_support import seed_family, unique_openid, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run portal tests",
    ),
]

PASSWORD = "portal-password"
DAY = date(2026, 11, 16)  # 周一；课表窗口的绝对锚点


@dataclass(frozen=True, slots=True)
class PortalSeed:
    tenant_id: UUID
    student_id: UUID
    other_student_id: UUID
    course_id: UUID
    class_id: UUID
    admin_phone: str
    teacher_phone: str
    openid: str
    lesson_normal: UUID
    lesson_moved: UUID
    lesson_cancelled: UUID
    lesson_outside: UUID
    lesson_makeup: UUID
    purchase_main: UUID
    purchase_expired: UUID


def slot(day_offset: int, sh: int, eh: int) -> tuple[datetime, datetime]:
    day = DAY + timedelta(days=day_offset)
    return (
        datetime.combine(day, time(sh, 0), tzinfo=BUSINESS_TZ),
        datetime.combine(day, time(eh, 0), tzinfo=BUSINESS_TZ),
    )


def window() -> dict[str, str]:
    """一周窗口：DAY 00:00 → DAY+7 00:00（含头不含尾）。"""

    start = datetime.combine(DAY, time(0, 0), tzinfo=BUSINESS_TZ)
    return {"from": start.isoformat(), "to": (start + timedelta(days=7)).isoformat()}


async def login_parent(client: httpx.AsyncClient, openid: str) -> dict[str, str]:
    response = await client.post("/auth/wechat-login", json={"wx_code": openid})
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def login_staff(client: httpx.AsyncClient, phone: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"phone": phone, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def add_staff(engine, tenant_id: UUID, role: str, teacher_id: UUID | None = None) -> str:
    phone = unique_phone()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO staff_account "
                "(id, tenant_id, name, phone, password_hash, role, teacher_id, status) "
                "VALUES (:id, :t, :name, :phone, :hash, :role, :teacher, 'active')"
            ),
            {
                "id": uuid4(),
                "t": tenant_id,
                "name": f"{role} user",
                "phone": phone,
                "hash": hash_password(PASSWORD),
                "role": role,
                "teacher": teacher_id,
            },
        )
    return phone


async def insert_lesson(
    engine,
    *,
    tenant_id: UUID,
    class_id: UUID,
    classroom_id: UUID,
    teacher_id: UUID,
    start_at: datetime,
    end_at: datetime,
    status: str = "normal",
) -> UUID:
    lesson_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO lesson "
                "(id, tenant_id, class_id, classroom_id, teacher_id, start_at, end_at, status) "
                "VALUES (:id, :t, :c, :r, :th, :s, :e, :status)"
            ),
            {
                "id": lesson_id,
                "t": tenant_id,
                "c": class_id,
                "r": classroom_id,
                "th": teacher_id,
                "s": start_at,
                "e": end_at,
                "status": status,
            },
        )
    return lesson_id


@pytest.fixture
async def seed(owner_engine) -> PortalSeed:
    """一个机构、一个家长、一个在读孩子，外加另一个机构的孩子当越权靶子。"""

    openid = unique_openid("portal-parent")
    family = await seed_family(owner_engine, entries=[("家长端机构", "小明")], openid=openid)
    other_openid = unique_openid("portal-other")
    other_family = await seed_family(
        owner_engine, entries=[("另一机构", "小红")], openid=other_openid
    )

    tenant_id = family.tenant_ids[0]
    student_id = family.student_ids[0]
    other_student_id = other_family.student_ids[0]

    teacher1, teacher2 = uuid4(), uuid4()
    classroom1, classroom2 = uuid4(), uuid4()
    course_id, class_a, class_b = uuid4(), uuid4(), uuid4()

    async with owner_engine.begin() as connection:
        for teacher_id in (teacher1, teacher2):
            await connection.execute(
                text("INSERT INTO teacher (id, tenant_id, name) VALUES (:id, :t, '李老师')"),
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
            text(
                "INSERT INTO course (id, tenant_id, name, subject) "
                "VALUES (:id, :t, '物理冲刺', '物理')"
            ),
            {"id": course_id, "t": tenant_id},
        )
        for class_id, teacher_id, classroom_id, name in (
            (class_a, teacher1, classroom1, "冲刺A班"),
            (class_b, teacher2, classroom2, "冲刺B班"),
        ):
            await connection.execute(
                text(
                    "INSERT INTO class "
                    "(id, tenant_id, course_id, teacher_id, classroom_id, name) "
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
        # 孩子只在 A 班；B 班是补课靶子（补课席位可以排进没报名的班）。
        await connection.execute(
            text(
                "INSERT INTO class_enrollment (id, tenant_id, class_id, student_id, joined_at) "
                "VALUES (:id, :t, :c, :s, :j)"
            ),
            {
                "id": uuid4(),
                "t": tenant_id,
                "c": class_a,
                "s": student_id,
                "j": DAY - timedelta(days=30),
            },
        )

    start, end = slot(0, 18, 19)
    lesson_normal = await insert_lesson(
        owner_engine,
        tenant_id=tenant_id,
        class_id=class_a,
        classroom_id=classroom1,
        teacher_id=teacher1,
        start_at=start,
        end_at=end,
    )
    moved_start, moved_end = slot(1, 18, 19)
    lesson_moved = await insert_lesson(
        owner_engine,
        tenant_id=tenant_id,
        class_id=class_a,
        classroom_id=classroom1,
        teacher_id=teacher1,
        start_at=moved_start,
        end_at=moved_end,
        status="moved",
    )
    cancelled_start, cancelled_end = slot(2, 18, 19)
    lesson_cancelled = await insert_lesson(
        owner_engine,
        tenant_id=tenant_id,
        class_id=class_a,
        classroom_id=classroom1,
        teacher_id=teacher1,
        start_at=cancelled_start,
        end_at=cancelled_end,
        status="cancelled",
    )
    outside_start, outside_end = slot(10, 18, 19)
    lesson_outside = await insert_lesson(
        owner_engine,
        tenant_id=tenant_id,
        class_id=class_a,
        classroom_id=classroom1,
        teacher_id=teacher1,
        start_at=outside_start,
        end_at=outside_end,
    )
    makeup_start, makeup_end = slot(3, 19, 20)
    lesson_makeup = await insert_lesson(
        owner_engine,
        tenant_id=tenant_id,
        class_id=class_b,
        classroom_id=classroom2,
        teacher_id=teacher2,
        start_at=makeup_start,
        end_at=makeup_end,
    )
    # 另一个机构的孩子：同一时间窗，绝不能出现在小明家长的结果里。
    other_teacher, other_classroom, other_course, other_class = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    other_tenant = other_family.tenant_ids[0]
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO teacher (id, tenant_id, name) VALUES (:id, :t, '王老师')"),
            {"id": other_teacher, "t": other_tenant},
        )
        await connection.execute(
            text(
                "INSERT INTO classroom (id, tenant_id, name, capacity) "
                "VALUES (:id, :t, '别家教室', 20)"
            ),
            {"id": other_classroom, "t": other_tenant},
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
                "r": other_classroom,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO class_enrollment (id, tenant_id, class_id, student_id, joined_at) "
                "VALUES (:id, :t, :c, :s, :j)"
            ),
            {
                "id": uuid4(),
                "t": other_tenant,
                "c": other_class,
                "s": other_student_id,
                "j": DAY - timedelta(days=30),
            },
        )
    await insert_lesson(
        owner_engine,
        tenant_id=other_tenant,
        class_id=other_class,
        classroom_id=other_classroom,
        teacher_id=other_teacher,
        start_at=start,
        end_at=end,
    )

    # 补课席位：孩子没在 B 班报名，但有一张 makeup 考勤座。
    async with owner_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO attendance "
                "(id, tenant_id, lesson_id, student_id, status, deduct_lessons, operator_id) "
                "VALUES (:id, :t, :l, :s, 'makeup', 0, :op)"
            ),
            {
                "id": uuid4(),
                "t": tenant_id,
                "l": lesson_makeup,
                "s": student_id,
                "op": teacher1,
            },
        )

    admin_phone = await add_staff(owner_engine, tenant_id, "admin")
    teacher_phone = await add_staff(owner_engine, tenant_id, "teacher", teacher1)

    return PortalSeed(
        tenant_id=tenant_id,
        student_id=student_id,
        other_student_id=other_student_id,
        course_id=course_id,
        class_id=class_a,
        admin_phone=admin_phone,
        teacher_phone=teacher_phone,
        openid=openid,
        lesson_normal=lesson_normal,
        lesson_moved=lesson_moved,
        lesson_cancelled=lesson_cancelled,
        lesson_outside=lesson_outside,
        lesson_makeup=lesson_makeup,
        purchase_main=uuid4(),
        purchase_expired=uuid4(),
    )


async def buy(
    client: httpx.AsyncClient,
    seed: PortalSeed,
    *,
    lesson_count: int,
    valid_months: int = 12,
) -> str:
    """通过管理端缴费接口造包（顺带写 purchase 流水，家长端看到的就是真数据）。"""

    headers = await login_staff(client, seed.admin_phone)
    response = await client.post(
        "/purchases",
        json={
            "student_id": str(seed.student_id),
            "course_id": str(seed.course_id),
            "lesson_count": lesson_count,
            "gift_lesson_count": 0,
            "valid_months": valid_months,
            "amount": "2400.00",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def add_consume(engine, seed: PortalSeed, *, lesson_id: UUID, when: datetime) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO lesson_ledger "
                "(id, tenant_id, student_id, purchase_id, lesson_id, type, delta, reason, "
                "operator_id, created_at) "
                "VALUES (:id, :t, :s, :p, :l, 'consume', -1, '上课扣课', :op, :at)"
            ),
            {
                "id": uuid4(),
                "t": seed.tenant_id,
                "s": seed.student_id,
                "p": seed.purchase_main,
                "l": lesson_id,
                "op": uuid4(),
                "at": when,
            },
        )


# ------------------------------------------------------------------ 课表


async def test_schedule_rejects_parent_without_active_student(client, owner_engine) -> None:
    """两个绑定、未选孩子 → tenant_id 为空 → 403 OUT_OF_SCOPE（不是 TENANT_CONTEXT_REQUIRED）。"""

    openid = unique_openid("portal-nopick")
    await seed_family(owner_engine, entries=[("多娃甲", "甲"), ("多娃乙", "乙")], openid=openid)
    response = await client.post("/auth/wechat-login", json={"wx_code": openid})
    body = response.json()
    assert body["tenant_id"] is None
    headers = {"Authorization": f"Bearer {body['access_token']}"}

    for path in ("/me/schedule", "/me/balance", "/me/ledger"):
        query = window() if path == "/me/schedule" else None
        res = await client.get(path, params=query, headers=headers)
        assert res.status_code == 403, (path, res.text)
        assert res.json()["error"]["code"] == "OUT_OF_SCOPE"


async def test_schedule_requires_timezone(client, seed: PortalSeed) -> None:
    headers = await login_parent(client, seed.openid)
    response = await client.get(
        "/me/schedule",
        params={"from": "2026-11-16T00:00:00", "to": "2026-11-23T00:00:00"},
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TIMEZONE_REQUIRED"


async def test_schedule_window_capped_at_31_days(client, seed: PortalSeed) -> None:
    headers = await login_parent(client, seed.openid)
    start = datetime.combine(DAY, time(0, 0), tzinfo=BUSINESS_TZ)
    response = await client.get(
        "/me/schedule",
        params={
            "from": start.isoformat(),
            "to": (start + timedelta(days=40)).isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "RANGE_TOO_LARGE"


async def test_schedule_rejects_reversed_range(client, seed: PortalSeed) -> None:
    headers = await login_parent(client, seed.openid)
    start = datetime.combine(DAY, time(0, 0), tzinfo=BUSINESS_TZ)
    response = await client.get(
        "/me/schedule",
        params={
            "from": (start + timedelta(days=7)).isoformat(),
            "to": start.isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "RANGE_INVALID"


async def test_schedule_hides_cancelled_and_outside_window(client, seed: PortalSeed) -> None:
    headers = await login_parent(client, seed.openid)
    response = await client.get("/me/schedule", params=window(), headers=headers)
    body = response.json()
    assert response.status_code == 200, response.text

    ids = {item["lesson_id"] for item in body["items"]}
    assert str(seed.lesson_normal) in ids
    assert str(seed.lesson_moved) in ids
    assert str(seed.lesson_makeup) in ids
    # 031 口径：cancelled 对家长没有意义，窗口外的也不返回。
    assert str(seed.lesson_cancelled) not in ids
    assert str(seed.lesson_outside) not in ids
    assert body["student_id"] == str(seed.student_id)
    assert body["from"].endswith("+08:00")
    assert body["to"].endswith("+08:00")


async def test_schedule_marks_moved_and_makeup(client, seed: PortalSeed) -> None:
    headers = await login_parent(client, seed.openid)
    body = (await client.get("/me/schedule", params=window(), headers=headers)).json()
    by_id = {item["lesson_id"]: item for item in body["items"]}

    moved = by_id[str(seed.lesson_moved)]
    assert moved["status"] == "moved"
    assert moved["class_name"] == "冲刺A班"
    assert moved["subject"] == "物理"
    assert moved["teacher_name"] == "李老师"
    assert moved["start_at"].endswith("+08:00")

    # 补课排进了孩子没报名的 B 班，仍然要出现，并且状态是 makeup（不是 normal）。
    makeup = by_id[str(seed.lesson_makeup)]
    assert makeup["status"] == "makeup"
    assert makeup["class_name"] == "冲刺B班"


async def test_schedule_never_leaks_other_child(client, seed: PortalSeed) -> None:
    """同窗口另一机构的孩子有课，小明的结果里必须一条都不能带出来。"""

    headers = await login_parent(client, seed.openid)
    body = (await client.get("/me/schedule", params=window(), headers=headers)).json()
    assert len(body["items"]) == 3  # normal + moved + makeup
    for item in body["items"]:
        assert item["class_name"] in {"冲刺A班", "冲刺B班"}


async def test_schedule_dies_when_binding_is_removed(
    client, owner_engine, seed: PortalSeed
) -> None:
    """token 里的 active_student_id 还在，但绑定被解除 → 立刻 403（不靠 token 自觉）。"""

    headers = await login_parent(client, seed.openid)
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM parent_student WHERE student_id = :s"),
            {"s": seed.student_id},
        )
    response = await client.get("/me/schedule", params=window(), headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "OUT_OF_SCOPE"


# ------------------------------------------------------------------ 余额


async def test_balance_matches_admin_view(client, owner_engine, seed: PortalSeed) -> None:
    """家长看到的 total 必须与后台 /students/{id}/balance 一模一样。"""

    seed = replace_purchase(seed, await buy(client, seed, lesson_count=20))
    await add_consume(owner_engine, seed, lesson_id=seed.lesson_normal, when=slot(0, 19, 20)[0])
    await add_consume(owner_engine, seed, lesson_id=seed.lesson_moved, when=slot(1, 19, 20)[0])

    admin_headers = await login_staff(client, seed.admin_phone)
    admin_balance = await client.get(f"/students/{seed.student_id}/balance", headers=admin_headers)
    assert admin_balance.status_code == 200, admin_balance.text

    parent_headers = await login_parent(client, seed.openid)
    parent_balance = await client.get("/me/balance", headers=parent_headers)
    parent_body = parent_balance.json()
    assert parent_balance.status_code == 200, parent_balance.text

    assert parent_body["total"] == admin_balance.json()["total"] == 18
    admin_packages = admin_balance.json()["packages"]
    assert len(parent_body["packages"]) == len(admin_packages) == 1
    assert parent_body["packages"][0]["remaining"] == admin_packages[0]["remaining"] == 18
    assert parent_body["packages"][0]["purchase_id"] == admin_packages[0]["purchase_id"]
    assert parent_body["packages"][0]["expires_at"] == admin_packages[0]["expires_at"]


async def test_balance_skips_expired_package_but_keeps_its_lessons(
    client, owner_engine, seed: PortalSeed
) -> None:
    seed = replace_purchase(seed, await buy(client, seed, lesson_count=20))
    expired_purchase = await buy(client, seed, lesson_count=5, valid_months=1)
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE purchase SET expires_at = :d WHERE id = :id"),
            {"d": business_today() - timedelta(days=1), "id": expired_purchase},
        )

    headers = await login_parent(client, seed.openid)
    body = (await client.get("/me/balance", headers=headers)).json()
    # 过期包不进列表…
    assert [item["purchase_id"] for item in body["packages"]] == [str(seed.purchase_main)]
    # …但它那 5 节课仍在账上（SUM(delta) 口径，与先过期先扣一致）。
    assert body["total"] == 25


async def test_balance_reports_negative_total(client, owner_engine, seed: PortalSeed) -> None:
    seed = replace_purchase(seed, await buy(client, seed, lesson_count=2))
    async with owner_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO lesson_ledger "
                "(id, tenant_id, student_id, purchase_id, lesson_id, type, delta, reason, "
                "operator_id) "
                "VALUES (:id, :t, :s, NULL, NULL, 'adjust', -5, '手工调减', :op)"
            ),
            {"id": uuid4(), "t": seed.tenant_id, "s": seed.student_id, "op": uuid4()},
        )

    headers = await login_parent(client, seed.openid)
    body = (await client.get("/me/balance", headers=headers)).json()
    assert body["total"] == -3  # 欠课时照实返回，不裁剪成 0


# ------------------------------------------------------------------ 明细


async def test_ledger_filters_by_type(client, owner_engine, seed: PortalSeed) -> None:
    seed = replace_purchase(seed, await buy(client, seed, lesson_count=20))
    await add_consume(owner_engine, seed, lesson_id=seed.lesson_normal, when=slot(0, 19, 20)[0])

    headers = await login_parent(client, seed.openid)
    all_rows = (await client.get("/me/ledger", headers=headers)).json()
    assert all_rows["total"] == 2  # purchase + consume
    assert {item["type"] for item in all_rows["items"]} == {"purchase", "consume"}

    consumes = (await client.get("/me/ledger", params={"type": "consume"}, headers=headers)).json()
    assert consumes["total"] == 1
    assert consumes["items"][0]["delta"] == -1
    assert consumes["items"][0]["class_name"] == "冲刺A班"
    assert consumes["items"][0]["created_at"].endswith("+08:00")


async def test_ledger_pagination_and_page_size_cap(client, owner_engine, seed: PortalSeed) -> None:
    seed = replace_purchase(seed, await buy(client, seed, lesson_count=30))
    for index in range(24):
        await add_consume(
            owner_engine,
            seed,
            lesson_id=seed.lesson_normal,
            when=slot(0, 19, 20)[0] + timedelta(minutes=index + 1),
        )

    headers = await login_parent(client, seed.openid)
    first = (await client.get("/me/ledger", headers=headers)).json()
    assert first["total"] == 25
    assert len(first["items"]) == 20  # 默认 page_size=20

    second = (await client.get("/me/ledger", params={"page": 2}, headers=headers)).json()
    assert len(second["items"]) == 5
    assert second["total"] == 25
    first_stamps = {item["created_at"] for item in first["items"]}
    second_stamps = {item["created_at"] for item in second["items"]}
    assert first_stamps.isdisjoint(second_stamps)

    beyond = (await client.get("/me/ledger", params={"page": 99}, headers=headers)).json()
    assert beyond["items"] == []
    assert beyond["total"] == 25

    oversized = await client.get("/me/ledger", params={"page_size": 101}, headers=headers)
    assert oversized.status_code == 422


async def test_ledger_ignores_client_side_student_id(client, seed: PortalSeed) -> None:
    """红线：客户端传 student_id 一律不认，只认 token 里的激活孩子。"""

    headers = await login_parent(client, seed.openid)
    body = (
        await client.get(
            "/me/ledger",
            params={"student_id": str(seed.other_student_id)},
            headers=headers,
        )
    ).json()
    assert body["total"] == 0
    assert body["items"] == []


# ------------------------------------------------------------------ 角色闸


async def test_admin_and_teacher_cannot_use_parent_endpoints(client, seed: PortalSeed) -> None:
    for phone in (seed.admin_phone, seed.teacher_phone):
        headers = await login_staff(client, phone)
        for path in ("/me/schedule", "/me/balance", "/me/ledger"):
            params = window() if path == "/me/schedule" else None
            response = await client.get(path, params=params, headers=headers)
            assert response.status_code == 403, (phone, path, response.text)
            assert response.json()["error"]["code"] == "OUT_OF_SCOPE"


# ------------------------------------------------------------------ 辅助


def replace_purchase(seed: PortalSeed, purchase_id: str) -> PortalSeed:
    """把 fixture 里占位的 purchase_id 换成真实缴费产生的那个（扣课流水要挂到它上面）。"""

    return dataclasses.replace(seed, purchase_main=UUID(purchase_id))
