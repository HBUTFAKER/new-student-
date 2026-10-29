"""Schedule rules: recurrence pattern only (step 3.1).

Lesson generation (3.2) and overlap detection (3.3) are out of scope, so every
assertion here is about the rule rows themselves.
"""

import os
from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from app.core.security import hash_password
from tests.auth_support import unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run schedule tests",
    ),
]

PASSWORD = "schedule-password"
# 2026-11-03 是周二（weekday=1）；含首尾 8 周的最后一天是 2026-12-28。
START_DATE = date(2026, 11, 3)
EIGHT_WEEKS_END = "2026-12-28"


@dataclass(frozen=True, slots=True)
class ScheduleSeed:
    tenant_id: UUID
    admin_phone: str
    registrar_phone: str
    teacher_phone: str
    teacher_id: UUID
    classroom_id: UUID
    class_id: UUID
    ended_class_id: UUID


async def login(client: httpx.AsyncClient, phone: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"phone": phone, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def schedule_seed(owner_engine) -> ScheduleSeed:
    tenant_id = uuid4()
    teacher_id, classroom_id, course_id = uuid4(), uuid4(), uuid4()
    class_id, ended_class_id = uuid4(), uuid4()
    admin_phone, registrar_phone, teacher_phone = (
        unique_phone(),
        unique_phone(),
        unique_phone(),
    )
    password_hash = hash_password(PASSWORD)

    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
            {"id": tenant_id, "name": f"排课机构-{uuid4().hex[:8]}"},
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
            (ended_class_id, "去年秋季班", "ended"),
        ):
            await connection.execute(
                text(
                    "INSERT INTO class "
                    "(id, tenant_id, course_id, teacher_id, classroom_id, name, "
                    " term_start, term_end, status) "
                    "VALUES (:id, :tenant, :course, :teacher, :room, :name, "
                    " '2026-09-01', '2027-01-31', :status)"
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
                    "teacher": linked,
                },
            )

    return ScheduleSeed(
        tenant_id=tenant_id,
        admin_phone=admin_phone,
        registrar_phone=registrar_phone,
        teacher_phone=teacher_phone,
        teacher_id=teacher_id,
        classroom_id=classroom_id,
        class_id=class_id,
        ended_class_id=ended_class_id,
    )


def rule_payload(seed: ScheduleSeed, **overrides) -> dict:
    """默认「每周二四 18:00–19:30 共 8 周」；值为 None 的键会被丢弃，便于造缺字段用例。"""

    data: dict = {
        "class_id": str(seed.class_id),
        "classroom_id": str(seed.classroom_id),
        "teacher_id": str(seed.teacher_id),
        "weekdays": [1, 3],
        "start_time": "18:00:00",
        "end_time": "19:30:00",
        "start_date": START_DATE.isoformat(),
        "weeks": 8,
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


# -------------------------------------------------------------- happy path


async def test_tuesday_thursday_eight_weeks_rule_crud(
    client, owner_engine, schedule_seed: ScheduleSeed
) -> None:
    seed = schedule_seed
    headers = await login(client, seed.admin_phone)

    created = await client.post("/schedule-rules", json=rule_payload(seed), headers=headers)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["total"] == 2
    assert [item["weekday"] for item in body["items"]] == [1, 3]
    assert [item["end_date"] for item in body["items"]] == [EIGHT_WEEKS_END] * 2

    tuesday = body["items"][0]
    assert tuesday["class_id"] == str(seed.class_id)
    assert tuesday["class_name"] == "初二物理冲刺班"
    assert tuesday["teacher_name"] == "排课老师"
    assert tuesday["classroom_name"] == "多媒体教室"
    assert tuesday["start_time"] == "18:00:00"
    assert tuesday["end_time"] == "19:30:00"
    assert tuesday["start_date"] == START_DATE.isoformat()

    # 列表按 weekday 排序，可按班/老师/教室/星期过滤。
    listing = await client.get(
        "/schedule-rules", params={"class_id": str(seed.class_id)}, headers=headers
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 2

    thursday = await client.get("/schedule-rules", params={"weekday": 3}, headers=headers)
    assert [item["weekday"] for item in thursday.json()["items"]] == [3]

    detail = await client.get(f"/schedule-rules/{tuesday['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["weekday"] == 1

    # 改时间：只影响这一条（周四那条不变）。
    patched = await client.patch(
        f"/schedule-rules/{tuesday['id']}", json={"end_time": "20:00:00"}, headers=headers
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["end_time"] == "20:00:00"
    still_two = await client.get(
        "/schedule-rules", params={"class_id": str(seed.class_id)}, headers=headers
    )
    assert [item["end_time"] for item in still_two.json()["items"]] == ["20:00:00", "19:30:00"]

    removed = await client.delete(f"/schedule-rules/{tuesday['id']}", headers=headers)
    assert removed.status_code == 204
    gone = await client.get(f"/schedule-rules/{tuesday['id']}", headers=headers)
    assert gone.status_code == 404
    left = await client.get(
        "/schedule-rules", params={"class_id": str(seed.class_id)}, headers=headers
    )
    assert [item["weekday"] for item in left.json()["items"]] == [3]

    # 3.1 只落规律，不生成课次（3.2 才做）。
    async with owner_engine.connect() as connection:
        lessons = await connection.execute(
            text("SELECT count(*) FROM lesson WHERE class_id = :id"), {"id": seed.class_id}
        )
        assert lessons.scalar_one() == 0


async def test_single_weekday_with_explicit_end_date(client, schedule_seed: ScheduleSeed) -> None:
    """05 文档的单个 weekday + end_date 请求体仍然成立（响应结构见差异说明）。"""

    seed = schedule_seed
    headers = await login(client, seed.admin_phone)
    created = await client.post(
        "/schedule-rules",
        json=rule_payload(
            seed,
            weekdays=None,
            weekday=2,
            start_time="19:00:00",
            end_time="21:00:00",
            start_date="2026-11-04",
            weeks=None,
            end_date="2027-01-31",
        ),
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["total"] == 1
    assert body["items"][0]["weekday"] == 2
    assert body["items"][0]["end_date"] == "2027-01-31"

    # end_date 可清空（规律变成长期有效）。
    cleared = await client.patch(
        f"/schedule-rules/{body['items'][0]['id']}", json={"end_date": None}, headers=headers
    )
    assert cleared.status_code == 200
    assert cleared.json()["end_date"] is None


# ---------------------------------------------------------------- validation


async def test_rule_field_validation_rejects_bad_input(client, schedule_seed: ScheduleSeed) -> None:
    seed = schedule_seed
    headers = await login(client, seed.admin_phone)

    cases = [
        # weekday 边界与来源二选一
        (rule_payload(seed, weekdays=None, weekday=7), 422, "VALIDATION_ERROR"),
        (rule_payload(seed, weekdays=None, weekday=-1), 422, "VALIDATION_ERROR"),
        (rule_payload(seed, weekdays=[3, 9]), 422, "VALIDATION_ERROR"),
        (rule_payload(seed, weekday=2), 422, "VALIDATION_ERROR"),
        (rule_payload(seed, weekdays=None), 422, "VALIDATION_ERROR"),
        (rule_payload(seed, weekdays=[2, 2]), 422, "VALIDATION_ERROR"),
        # 周数与结束日期
        (rule_payload(seed, weeks=0), 422, "VALIDATION_ERROR"),
        (rule_payload(seed, end_date="2026-12-31"), 422, "VALIDATION_ERROR"),
        (rule_payload(seed, weeks=None), 422, "VALIDATION_ERROR"),
        # 时间与日期区间
        (rule_payload(seed, start_time="19:30:00", end_time="18:00:00"), 422, "TIME_RANGE_INVALID"),
        (rule_payload(seed, start_time="18:00:00", end_time="18:00:00"), 422, "TIME_RANGE_INVALID"),
        (rule_payload(seed, weeks=None, end_date="2026-10-31"), 422, "DATE_RANGE_INVALID"),
        # 引用必须存在且可用
        (rule_payload(seed, class_id=str(uuid4())), 404, "CLASS_NOT_FOUND"),
        (rule_payload(seed, classroom_id=str(uuid4())), 404, "CLASSROOM_NOT_FOUND"),
        (rule_payload(seed, teacher_id=str(uuid4())), 404, "TEACHER_NOT_FOUND"),
        (rule_payload(seed, class_id=str(seed.ended_class_id)), 409, "CLASS_ENDED"),
    ]
    for payload, status, code in cases:
        response = await client.post("/schedule-rules", json=payload, headers=headers)
        assert response.status_code == status, (payload, response.text)
        assert response.json()["error"]["code"] == code, (payload, response.text)

    # 全部被拒：一条规律都没落库。
    listing = await client.get(
        "/schedule-rules", params={"class_id": str(seed.class_id)}, headers=headers
    )
    assert listing.json()["total"] == 0


async def test_patch_revalidates_merged_values(client, schedule_seed: ScheduleSeed) -> None:
    seed = schedule_seed
    headers = await login(client, seed.admin_phone)
    created = await client.post("/schedule-rules", json=rule_payload(seed), headers=headers)
    tuesday = created.json()["items"][0]
    url = f"/schedule-rules/{tuesday['id']}"

    # 只改开始时间，合并后的 end_time 仍是 19:30 → 非法。
    late_start = await client.patch(url, json={"start_time": "21:00:00"}, headers=headers)
    assert late_start.status_code == 422
    assert late_start.json()["error"]["code"] == "TIME_RANGE_INVALID"

    early_end = await client.patch(url, json={"end_date": "2026-10-01"}, headers=headers)
    assert early_end.status_code == 422
    assert early_end.json()["error"]["code"] == "DATE_RANGE_INVALID"

    # 必填字段不允许显式置空，class_id 不在可改字段内（传了也不生效）。
    for field in ("weekday", "start_time", "end_time", "start_date", "teacher_id"):
        nulled = await client.patch(url, json={field: None}, headers=headers)
        assert nulled.status_code == 422, (field, nulled.text)
        assert nulled.json()["error"]["code"] == "VALIDATION_ERROR"

    untouched = await client.get(url, headers=headers)
    assert untouched.json()["start_time"] == "18:00:00"
    assert untouched.json()["end_date"] == EIGHT_WEEKS_END


# ------------------------------------------------------------ tenant boundary


async def test_cross_tenant_rules_are_invisible(
    client, owner_engine, schedule_seed: ScheduleSeed
) -> None:
    seed = schedule_seed
    other_tenant, foreign_teacher, foreign_room = uuid4(), uuid4(), uuid4()
    foreign_course, foreign_class, foreign_rule = uuid4(), uuid4(), uuid4()

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
                "INSERT INTO schedule_rule "
                "(id, tenant_id, class_id, classroom_id, teacher_id, weekday, "
                " start_time, end_time, start_date, end_date) "
                "VALUES (:id, :tenant, :class, :room, :teacher, 1, "
                " '18:00', '19:30', '2026-11-03', '2026-12-28')"
            ),
            {
                "id": foreign_rule,
                "tenant": other_tenant,
                "class": foreign_class,
                "room": foreign_room,
                "teacher": foreign_teacher,
            },
        )

    headers = await login(client, seed.admin_phone)

    for method in ("get", "delete"):
        response = await getattr(client, method)(f"/schedule-rules/{foreign_rule}", headers=headers)
        assert response.status_code == 404, (method, response.text)
        assert response.json()["error"]["code"] == "SCHEDULE_RULE_NOT_FOUND"

    patched = await client.patch(
        f"/schedule-rules/{foreign_rule}", json={"end_time": "20:00:00"}, headers=headers
    )
    assert patched.status_code == 404
    assert patched.json()["error"]["code"] == "SCHEDULE_RULE_NOT_FOUND"

    listing = await client.get("/schedule-rules", headers=headers)
    assert str(foreign_rule) not in {item["id"] for item in listing.json()["items"]}

    # 也不能拿别家的班级/教室/老师排自己的课。
    foreign_ref = await client.post(
        "/schedule-rules", json=rule_payload(seed, class_id=str(foreign_class)), headers=headers
    )
    assert foreign_ref.status_code == 404
    assert foreign_ref.json()["error"]["code"] == "CLASS_NOT_FOUND"


# ---------------------------------------------------------------- permissions


async def test_only_office_roles_can_manage_rules(client, schedule_seed: ScheduleSeed) -> None:
    seed = schedule_seed
    admin = await login(client, seed.admin_phone)
    created = await client.post("/schedule-rules", json=rule_payload(seed), headers=admin)
    assert created.status_code == 201, created.text
    rule_id = created.json()["items"][0]["id"]

    teacher = await login(client, seed.teacher_phone)
    denied = [
        await client.get("/schedule-rules", headers=teacher),
        await client.get(f"/schedule-rules/{rule_id}", headers=teacher),
        await client.post("/schedule-rules", json=rule_payload(seed), headers=teacher),
        await client.patch(
            f"/schedule-rules/{rule_id}", json={"end_time": "20:00"}, headers=teacher
        ),
        await client.delete(f"/schedule-rules/{rule_id}", headers=teacher),
    ]
    for response in denied:
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "OUT_OF_SCOPE"

    # 教务（registrar）在矩阵里是 ✅，读写都要放行。
    registrar = await login(client, seed.registrar_phone)
    posted = await client.post("/schedule-rules", json=rule_payload(seed), headers=registrar)
    assert posted.status_code == 201, posted.text
    assert (
        await client.patch(
            f"/schedule-rules/{rule_id}", json={"end_time": "20:00:00"}, headers=registrar
        )
    ).status_code == 200
    assert (await client.delete(f"/schedule-rules/{rule_id}", headers=registrar)).status_code == 204

    # 未登录直接 401。
    assert (await client.get("/schedule-rules")).status_code == 401
