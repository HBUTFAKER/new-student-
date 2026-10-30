"""Report aggregation (step 4.4): three read-only endpoints.

Numbers are checked against hand-built seed data, not against the implementation:

* 出勤率 = present / (present + leave + absent)，分母 0 返回 null；
* ``cancelled`` 课次与「未开始」课次不进任何数字；
* 课耗只认 ``type='consume'``，窗口按 ``created_at``，跨租户不可见；
* 老师课时按 ``start_at/end_at`` 实际差值求和，不读课程配置；
* teacher 角色 403，参数缺失/越界/不带时区 422。
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
from app.services.report import _rate
from tests.auth_support import unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run report tests",
    ),
]

PASSWORD = "report-password"


@dataclass(frozen=True, slots=True)
class ReportSeed:
    tenant_a: UUID
    tenant_b: UUID
    admin_a_phone: str
    admin_b_phone: str
    teacher_a_phone: str
    class_a: UUID
    class_b: UUID
    class_c: UUID
    class_b_only: UUID  # 租户 B 的班级，用来验证跨租户不可见


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=BUSINESS_TZ)


def window(days_back: int = 7, days_ahead: int = 7) -> tuple[datetime, datetime]:
    today = business_today()
    return at(today - timedelta(days=days_back), 0), at(today + timedelta(days=days_ahead), 0)


async def login(client: httpx.AsyncClient, phone: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"phone": phone, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def insert_lesson(
    connection,
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
    await connection.execute(
        text(
            "INSERT INTO lesson "
            "(id, tenant_id, class_id, classroom_id, teacher_id, start_at, end_at, status) "
            "VALUES (:id, :t, :c, :r, :th, :s, :e, :st)"
        ),
        {
            "id": lesson_id,
            "t": tenant_id,
            "c": class_id,
            "r": classroom_id,
            "th": teacher_id,
            "s": start_at,
            "e": end_at,
            "st": status,
        },
    )
    return lesson_id


async def insert_attendance(
    connection,
    *,
    tenant_id: UUID,
    lesson_id: UUID,
    student_id: UUID,
    status: str,
    operator_id: UUID,
) -> None:
    await connection.execute(
        text(
            "INSERT INTO attendance "
            "(id, tenant_id, lesson_id, student_id, status, deduct_lessons, operator_id) "
            "VALUES (:id, :t, :l, :s, :st, 0, :op)"
        ),
        {
            "id": uuid4(),
            "t": tenant_id,
            "l": lesson_id,
            "s": student_id,
            "st": status,
            "op": operator_id,
        },
    )


@pytest.fixture
async def report_seed(owner_engine) -> ReportSeed:
    today = business_today()
    tenant_a, tenant_b = uuid4(), uuid4()
    admin_a, admin_b = uuid4(), uuid4()
    teacher_a1, teacher_a2 = uuid4(), uuid4()
    teacher_b = uuid4()
    room_a1, room_a2, room_b = uuid4(), uuid4(), uuid4()
    course_a, course_b = uuid4(), uuid4()
    class_a, class_b, class_c, class_b_only = uuid4(), uuid4(), uuid4(), uuid4()
    s1, s2, s3, s4, s_b = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    package_a, package_b = uuid4(), uuid4()
    purchase_s1, purchase_s3, purchase_s_b = uuid4(), uuid4(), uuid4()
    admin_a_phone, admin_b_phone, teacher_a_phone = (
        unique_phone(),
        unique_phone(),
        unique_phone(),
    )
    password_hash = hash_password(PASSWORD)

    async with owner_engine.begin() as connection:
        for tenant_id, name in ((tenant_a, "报表机构A"), (tenant_b, "报表机构B")):
            await connection.execute(
                text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
                {"id": tenant_id, "name": name},
            )

        for teacher_id, tenant_id, name in (
            (teacher_a1, tenant_a, "A1老师"),
            (teacher_a2, tenant_a, "A2老师"),
            (teacher_b, tenant_b, "B老师"),
        ):
            await connection.execute(
                text("INSERT INTO teacher (id, tenant_id, name) VALUES (:id, :t, :name)"),
                {"id": teacher_id, "t": tenant_id, "name": name},
            )

        for classroom_id, tenant_id, name in (
            (room_a1, tenant_a, "A1教室"),
            (room_a2, tenant_a, "A2教室"),
            (room_b, tenant_b, "B教室"),
        ):
            await connection.execute(
                text(
                    "INSERT INTO classroom (id, tenant_id, name, capacity) "
                    "VALUES (:id, :t, :name, 20)"
                ),
                {"id": classroom_id, "t": tenant_id, "name": name},
            )

        for course_id, tenant_id, name in (
            (course_a, tenant_a, "A课程"),
            (course_b, tenant_b, "B课程"),
        ):
            await connection.execute(
                text("INSERT INTO course (id, tenant_id, name) VALUES (:id, :t, :name)"),
                {"id": course_id, "t": tenant_id, "name": name},
            )

        for class_id, tenant_id, course_id, teacher_id, room_id, name in (
            (class_a, tenant_a, course_a, teacher_a1, room_a1, "A班"),
            (class_b, tenant_a, course_a, teacher_a2, room_a2, "B班"),
            (class_c, tenant_a, course_a, teacher_a2, room_a2, "C班"),
            (class_b_only, tenant_b, course_b, teacher_b, room_b, "B机构班"),
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
                    "r": room_id,
                    "name": name,
                },
            )

        for student_id, tenant_id, name in (
            (s1, tenant_a, "学生一"),
            (s2, tenant_a, "学生二"),
            (s3, tenant_a, "学生三"),
            (s4, tenant_a, "学生四"),
            (s_b, tenant_b, "B机构学生"),
        ):
            await connection.execute(
                text("INSERT INTO student (id, tenant_id, name) VALUES (:id, :t, :name)"),
                {"id": student_id, "t": tenant_id, "name": name},
            )

        for class_id, student_id, tenant_id in (
            (class_a, s1, tenant_a),
            (class_a, s2, tenant_a),
            (class_a, s3, tenant_a),
            (class_b, s4, tenant_a),
            (class_b_only, s_b, tenant_b),
        ):
            await connection.execute(
                text(
                    "INSERT INTO class_enrollment "
                    "(id, tenant_id, class_id, student_id, joined_at) "
                    "VALUES (:id, :t, :c, :s, :joined)"
                ),
                {
                    "id": uuid4(),
                    "t": tenant_id,
                    "c": class_id,
                    "s": student_id,
                    "joined": today - timedelta(days=30),
                },
            )

        # 课次：A 班两节已开始（normal + moved）、一节 cancelled、一节未来；
        # B 班一节；C 班一节且没有任何考勤（用来验证分母 0 → null）。
        lesson_a1 = await insert_lesson(
            connection,
            tenant_id=tenant_a,
            class_id=class_a,
            classroom_id=room_a1,
            teacher_id=teacher_a1,
            start_at=at(today - timedelta(days=3), 19),
            end_at=at(today - timedelta(days=3), 20, 30),
        )
        lesson_a2 = await insert_lesson(
            connection,
            tenant_id=tenant_a,
            class_id=class_a,
            classroom_id=room_a1,
            teacher_id=teacher_a1,
            start_at=at(today - timedelta(days=2), 19),
            end_at=at(today - timedelta(days=2), 20, 30),
            status="moved",
        )
        lesson_cancelled = await insert_lesson(
            connection,
            tenant_id=tenant_a,
            class_id=class_a,
            classroom_id=room_a1,
            teacher_id=teacher_a1,
            start_at=at(today - timedelta(days=1), 19),
            end_at=at(today - timedelta(days=1), 20, 30),
            status="cancelled",
        )
        lesson_future = await insert_lesson(
            connection,
            tenant_id=tenant_a,
            class_id=class_a,
            classroom_id=room_a1,
            teacher_id=teacher_a1,
            start_at=at(today + timedelta(days=2), 19),
            end_at=at(today + timedelta(days=2), 20, 30),
        )
        lesson_b1 = await insert_lesson(
            connection,
            tenant_id=tenant_a,
            class_id=class_b,
            classroom_id=room_a2,
            teacher_id=teacher_a2,
            start_at=at(today - timedelta(days=2), 9),
            end_at=at(today - timedelta(days=2), 10, 30),
        )
        await insert_lesson(
            connection,
            tenant_id=tenant_a,
            class_id=class_c,
            classroom_id=room_a2,
            teacher_id=teacher_a2,
            start_at=at(today - timedelta(days=1), 9),
            end_at=at(today - timedelta(days=1), 10, 30),
        )
        lesson_b_tenant = await insert_lesson(
            connection,
            tenant_id=tenant_b,
            class_id=class_b_only,
            classroom_id=room_b,
            teacher_id=teacher_b,
            start_at=at(today - timedelta(days=1), 9),
            end_at=at(today - timedelta(days=1), 10, 30),
        )

        # A 班：3 present + 1 leave = 75%；cancelled / 未来课次的考勤不得计入。
        for lesson_id, student_id, status in (
            (lesson_a1, s1, "present"),
            (lesson_a1, s2, "present"),
            (lesson_a1, s3, "leave"),
            (lesson_a2, s1, "present"),
            (lesson_cancelled, s1, "present"),
            (lesson_future, s1, "present"),
        ):
            await insert_attendance(
                connection,
                tenant_id=tenant_a,
                lesson_id=lesson_id,
                student_id=student_id,
                status=status,
                operator_id=admin_a,
            )
        # B 班：一条 makeup（按到课计）+ 一条 absent → 50%，并在 notes 里注明。
        for lesson_id, student_id, status in (
            (lesson_b1, s1, "makeup"),
            (lesson_b1, s4, "absent"),
        ):
            await insert_attendance(
                connection,
                tenant_id=tenant_a,
                lesson_id=lesson_id,
                student_id=student_id,
                status=status,
                operator_id=admin_a,
            )
        await insert_attendance(
            connection,
            tenant_id=tenant_b,
            lesson_id=lesson_b_tenant,
            student_id=s_b,
            status="present",
            operator_id=admin_b,
        )

        for package_id, tenant_id, name in (
            (package_a, tenant_a, "A课时包"),
            (package_b, tenant_b, "B课时包"),
        ):
            await connection.execute(
                text(
                    "INSERT INTO course_package "
                    "(id, tenant_id, name, total_lessons, gift_lessons, price, valid_months) "
                    "VALUES (:id, :t, :name, 10, 0, 1000, 6)"
                ),
                {"id": package_id, "t": tenant_id, "name": name},
            )

        for purchase_id, tenant_id, student_id, course_id, package_id in (
            (purchase_s1, tenant_a, s1, course_a, package_a),
            (purchase_s3, tenant_a, s3, course_a, package_a),
            (purchase_s_b, tenant_b, s_b, course_b, package_b),
        ):
            await connection.execute(
                text(
                    "INSERT INTO purchase "
                    "(id, tenant_id, student_id, course_id, package_id, quantity, amount) "
                    "VALUES (:id, :t, :s, :c, :p, 1, 1000)"
                ),
                {
                    "id": purchase_id,
                    "t": tenant_id,
                    "s": student_id,
                    "c": course_id,
                    "p": package_id,
                },
            )

        # 课耗：s1 两节、s3 两节；窗口外的 consume 与 grant 都不该出现。
        for (
            tenant_id,
            operator_id,
            student_id,
            purchase_id,
            delta,
            lesson_id,
            entry_type,
            created_at,
        ) in (
            (
                tenant_a,
                admin_a,
                s1,
                purchase_s1,
                -1,
                lesson_a1,
                "consume",
                at(today - timedelta(days=2), 21),
            ),
            (
                tenant_a,
                admin_a,
                s1,
                purchase_s1,
                -1,
                lesson_a2,
                "consume",
                at(today - timedelta(days=1), 21),
            ),
            (
                tenant_a,
                admin_a,
                s3,
                purchase_s3,
                -2,
                lesson_a1,
                "consume",
                at(today - timedelta(days=1), 21),
            ),
            (
                tenant_a,
                admin_a,
                s1,
                purchase_s1,
                -1,
                lesson_a1,
                "consume",
                at(today - timedelta(days=30), 21),
            ),
            (
                tenant_a,
                admin_a,
                s1,
                purchase_s1,
                5,
                None,
                "grant",
                at(today - timedelta(days=2), 20),
            ),
            (
                tenant_b,
                admin_b,
                s_b,
                purchase_s_b,
                -1,
                lesson_b_tenant,
                "consume",
                at(today - timedelta(days=1), 21),
            ),
        ):
            await connection.execute(
                text(
                    "INSERT INTO lesson_ledger "
                    "(id, tenant_id, student_id, purchase_id, lesson_id, type, delta, "
                    "operator_id, created_at) "
                    "VALUES (:id, :t, :s, :p, :l, :ty, :d, :op, :cr)"
                ),
                {
                    "id": uuid4(),
                    "t": tenant_id,
                    "s": student_id,
                    "p": purchase_id,
                    "l": lesson_id,
                    "ty": entry_type,
                    "d": delta,
                    "op": operator_id,
                    "cr": created_at,
                },
            )

        for staff_id, tenant_id, phone, role, teacher_id in (
            (admin_a, tenant_a, admin_a_phone, "admin", None),
            (uuid4(), tenant_a, teacher_a_phone, "teacher", teacher_a1),
            (admin_b, tenant_b, admin_b_phone, "registrar", None),
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

    return ReportSeed(
        tenant_a=tenant_a,
        tenant_b=tenant_b,
        admin_a_phone=admin_a_phone,
        admin_b_phone=admin_b_phone,
        teacher_a_phone=teacher_a_phone,
        class_a=class_a,
        class_b=class_b,
        class_c=class_c,
        class_b_only=class_b_only,
    )


def params(seed: ReportSeed, **extra) -> dict[str, str]:
    start, end = window()
    query = {"from": start.isoformat(), "to": end.isoformat()}
    query.update(extra)
    return query


async def test_attendance_summary_aggregates_by_class(client, report_seed) -> None:
    headers = await login(client, report_seed.admin_a_phone)
    response = await client.get(
        "/reports/attendance-summary", params=params(report_seed), headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # 窗口回显用业务时区，字段名是 from/to（不是 from_）
    assert body["from"].endswith("+08:00")
    assert body["to"].endswith("+08:00")
    assert body["student_rows"] == []
    assert body["total"] == 3

    rows = {row["class_name"]: row for row in body["items"]}
    assert rows["A班"] == {
        "class_id": str(report_seed.class_a),
        "class_name": "A班",
        "lesson_count": 2,
        "present": 3,
        "leave": 1,
        "absent": 0,
        "attendance_rate": 75,
    }
    # makeup 按到课计；absent 进分母
    assert rows["B班"]["present"] == 1
    assert rows["B班"]["absent"] == 1
    assert rows["B班"]["attendance_rate"] == 50
    # 一节已开始的课但没有任何考勤：分母 0 → null，不是 0%
    assert rows["C班"]["lesson_count"] == 1
    assert rows["C班"]["attendance_rate"] is None
    assert any("makeup" in note for note in body["notes"])


async def test_attendance_summary_detail_excludes_cancelled_and_future(client, report_seed) -> None:
    headers = await login(client, report_seed.admin_a_phone)
    response = await client.get(
        "/reports/attendance-summary",
        params=params(report_seed, detail="true"),
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()

    students = {row["student_name"]: row for row in body["student_rows"]}
    # s1 在 A 班两节 + B 班 makeup = 3；cancelled 与未来课次上的两条 present 不计
    assert students["学生一"]["present"] == 3
    assert students["学生二"]["present"] == 1
    assert students["学生三"]["leave"] == 1
    assert students["学生四"]["absent"] == 1
    assert students["学生四"]["attendance_rate"] == 0
    assert "学生四" in students  # 有考勤的才展开


async def test_attendance_summary_class_filter(client, report_seed) -> None:
    headers = await login(client, report_seed.admin_a_phone)
    response = await client.get(
        "/reports/attendance-summary",
        params=params(report_seed, class_id=str(report_seed.class_a)),
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["class_name"] == "A班"


async def test_consumption_sums_consume_only(client, report_seed) -> None:
    headers = await login(client, report_seed.admin_a_phone)
    response = await client.get("/reports/consumption", params=params(report_seed), headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total"] == 2
    rows = {row["student_name"]: row for row in body["items"]}
    assert rows["学生一"]["total_consumed"] == 2
    assert rows["学生一"]["lesson_count"] == 2
    assert rows["学生三"]["total_consumed"] == 2
    assert rows["学生三"]["lesson_count"] == 1

    # by_class 之和等于 items（consume 流水自带 lesson_id）
    per_student: dict[str, int] = {}
    for row in body["by_class"]:
        per_student[row["student_id"]] = per_student.get(row["student_id"], 0) + row["consumed"]
    for row in body["items"]:
        assert per_student.get(row["student_id"]) == row["total_consumed"]
    assert {row["class_name"] for row in body["by_class"]} == {"A班"}


async def test_teacher_hours_uses_real_duration(client, report_seed) -> None:
    headers = await login(client, report_seed.admin_a_phone)
    response = await client.get(
        "/reports/teacher-hours", params=params(report_seed), headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total"] == 2
    rows = {row["teacher_name"]: row for row in body["items"]}
    # A1 老师：两节已开始 + 一节未来，cancelled 不计；每节 90 分钟
    assert rows["A1老师"]["lesson_count"] == 3
    assert rows["A1老师"]["completed_count"] == 2
    assert rows["A1老师"]["total_minutes"] == 270
    assert rows["A2老师"]["lesson_count"] == 2
    assert rows["A2老师"]["completed_count"] == 2
    assert rows["A2老师"]["total_minutes"] == 180


async def test_cross_tenant_isolation(client, report_seed) -> None:
    headers = await login(client, report_seed.admin_b_phone)

    attendance = await client.get(
        "/reports/attendance-summary", params=params(report_seed), headers=headers
    )
    assert attendance.status_code == 200
    names = [row["class_name"] for row in attendance.json()["items"]]
    assert names == ["B机构班"]

    consumption = await client.get(
        "/reports/consumption", params=params(report_seed), headers=headers
    )
    assert consumption.status_code == 200
    assert [row["student_name"] for row in consumption.json()["items"]] == ["B机构学生"]

    # 直接拿别家的 class_id 查：RLS 下返回空列表，不是别家数据
    filtered = await client.get(
        "/reports/attendance-summary",
        params=params(report_seed, class_id=str(report_seed.class_a)),
        headers=headers,
    )
    assert filtered.status_code == 200
    assert filtered.json()["items"] == []


async def test_teacher_forbidden(client, report_seed) -> None:
    headers = await login(client, report_seed.teacher_a_phone)
    for path in ("/reports/attendance-summary", "/reports/consumption", "/reports/teacher-hours"):
        response = await client.get(path, params=params(report_seed), headers=headers)
        assert response.status_code == 403, (path, response.text)
        assert response.json()["error"]["code"] == "OUT_OF_SCOPE"


async def test_window_validation(client, report_seed) -> None:
    headers = await login(client, report_seed.admin_a_phone)

    missing = await client.get("/reports/consumption", headers=headers)
    assert missing.status_code == 422

    reversed_ = await client.get(
        "/reports/consumption",
        params={
            "from": "2026-11-02T00:00:00+08:00",
            "to": "2026-11-01T00:00:00+08:00",
        },
        headers=headers,
    )
    assert reversed_.status_code == 422
    assert reversed_.json()["error"]["code"] == "RANGE_INVALID"

    too_wide = await client.get(
        "/reports/consumption",
        params={
            "from": "2025-01-01T00:00:00+08:00",
            "to": "2026-06-01T00:00:00+08:00",
        },
        headers=headers,
    )
    assert too_wide.status_code == 422
    assert too_wide.json()["error"]["code"] == "RANGE_TOO_LARGE"

    naive = await client.get(
        "/reports/consumption",
        params={"from": "2026-11-01", "to": "2026-12-01"},
        headers=headers,
    )
    assert naive.status_code == 422
    assert naive.json()["error"]["code"] == "TIMEZONE_REQUIRED"


def test_rate_rounds_half_up() -> None:
    assert _rate(0, 0, 0) is None
    assert _rate(3, 1, 0) == 75
    assert _rate(1, 0, 7) == 13  # 12.5% 四舍五入
    assert _rate(0, 0, 2) == 0
    assert _rate(2, 0, 0) == 100
