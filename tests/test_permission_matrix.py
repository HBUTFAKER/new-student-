"""权限矩阵收口（7.5 任务一）：把 02 矩阵逐行落成参数化测试。

三道闸门各测各的，不许混：

1. 租户闸门（红线一）：token A 访问租户 B 的资源 → 一律 404（不 403、不 2xx）。
2. 角色闸门：registrar/teacher/parent 打超出角色的写接口 → 403；老师「仅任教班级」
   用两个班级证明条件边界。
3. 数据归属闸门：parent 只看得见绑定集合，teacher 只看得见任教班。

与实现冲突的行按口径 1 处理：``xfail(strict=False)`` + 注释冲突点，全部进交接单。
覆盖对象 = 02 矩阵一期（「交付」列标「一期」）行。
"""

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from app.core.security import hash_password
from tests.auth_support import seed_staff, unique_openid, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run the permission matrix",
    ),
]

PASSWORD = "matrix-password"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass(frozen=True, slots=True)
class MatrixSeed:
    tenant_a: UUID
    tenant_b: UUID
    admin_a: str
    registrar_a: str
    teacher_a: str
    parent_a: str
    admin_b: str
    platform: str
    course_a: UUID
    classroom_a: UUID
    teacher_id: UUID
    student_a1: UUID
    student_a2: UUID
    student_a3: UUID
    parent_id: UUID
    parent2_id: UUID
    class_a1: UUID
    class_a2: UUID
    lesson_a: UUID
    purchase_id: UUID
    # 租户 B 靶机资源
    student_b: UUID
    class_b: UUID
    lesson_b: UUID
    teacher_b_id: UUID
    classroom_b: UUID
    course_b: UUID
    staff_b_id: UUID
    parent_b_id: UUID


async def _login(client: httpx.AsyncClient, phone: str) -> str:
    response = await client.post("/auth/login", json={"phone": phone, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


async def _parent_login(client: httpx.AsyncClient, openid: str) -> str:
    response = await client.post("/auth/wechat-login", json={"wx_code": openid})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture
async def matrix_seed(client: httpx.AsyncClient, owner_engine) -> MatrixSeed:
    """双租户 + 各角色 token + 代表性资源。"""

    tenant_a, tenant_b = uuid4(), uuid4()
    course_a, classroom_a = uuid4(), uuid4()
    teacher_id, teacher2_id = uuid4(), uuid4()
    student_a1, student_a2, student_a3 = uuid4(), uuid4(), uuid4()
    parent_id, parent2_id = uuid4(), uuid4()
    class_a1, class_a2 = uuid4(), uuid4()
    lesson_a = uuid4()
    admin_id, registrar_id, teacher_staff_id = uuid4(), uuid4(), uuid4()
    course_b, classroom_b, teacher_b_id = uuid4(), uuid4(), uuid4()
    student_b, class_b, lesson_b = uuid4(), uuid4(), uuid4()
    staff_b_id, parent_b_id = uuid4(), uuid4()

    admin_phone, registrar_phone, teacher_phone = unique_phone(), unique_phone(), unique_phone()
    admin_b_phone = unique_phone()
    parent_openid = unique_openid("matrix-parent")
    wechat_user_id = uuid4()
    pw_hash = hash_password(PASSWORD)
    today = datetime.now(UTC)

    async with owner_engine.begin() as conn:
        # ---- 租户 + 订阅 ----
        for tid, name in ((tenant_a, "矩阵机构A"), (tenant_b, "矩阵机构B")):
            await conn.execute(
                text("INSERT INTO tenant (id, name) VALUES (:id, :name)"), {"id": tid, "name": name}
            )
            plan_id = uuid4()
            await conn.execute(
                text(
                    "INSERT INTO plan (id, name, max_students, max_messages, price) "
                    "VALUES (:id, '基础版', 100, 1000, 0)"
                ),
                {"id": plan_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO tenant_subscription "
                    "(id, tenant_id, plan_id, start_date, end_date, status) "
                    "VALUES (:id, :t, :p, '2026-01-01', '2099-12-31', 'active')"
                ),
                {"id": uuid4(), "t": tid, "p": plan_id},
            )

        # ---- 租户 A：课程/教室/老师 ----
        await conn.execute(
            text(
                "INSERT INTO course (id, tenant_id, name, subject, duration_min, status) "
                "VALUES (:id, :t, '数学', '数学', 90, 'active')"
            ),
            {"id": course_a, "t": tenant_a},
        )
        await conn.execute(
            text(
                "INSERT INTO classroom (id, tenant_id, name, capacity) "
                "VALUES (:id, :t, '教室A', 20)"
            ),
            {"id": classroom_a, "t": tenant_a},
        )
        await conn.execute(
            text(
                "INSERT INTO teacher (id, tenant_id, name, status) "
                "VALUES (:id, :t, '老师甲', 'active')"
            ),
            {"id": teacher_id, "t": tenant_a},
        )
        await conn.execute(
            text(
                "INSERT INTO teacher (id, tenant_id, name, status) "
                "VALUES (:id, :t, '老师乙', 'active')"
            ),
            {"id": teacher2_id, "t": tenant_a},
        )
        # ---- 学生 ----
        for sid, name in ((student_a1, "学生甲"), (student_a2, "学生乙"), (student_a3, "学生丙")):
            await conn.execute(
                text(
                    "INSERT INTO student (id, tenant_id, name, status) "
                    "VALUES (:id, :t, :name, 'active')"
                ),
                {"id": sid, "t": tenant_a, "name": name},
            )
        # ---- 家长 + 绑定（parent_a 绑 a1，parent2 不绑） ----
        await conn.execute(
            text("INSERT INTO wechat_user (id, unionid) VALUES (:id, NULL)"), {"id": wechat_user_id}
        )
        await conn.execute(
            text(
                "INSERT INTO wechat_openid (id, wechat_user_id, app_id, openid) "
                "VALUES (:id, :u, 'wx_platform_stub', :openid)"
            ),
            {"id": uuid4(), "u": wechat_user_id, "openid": parent_openid},
        )
        await conn.execute(
            text(
                "INSERT INTO parent (id, tenant_id, name, phone, wechat_user_id) "
                "VALUES (:id, :t, '家长甲', '13800000001', :u)"
            ),
            {"id": parent_id, "t": tenant_a, "u": wechat_user_id},
        )
        await conn.execute(
            text(
                "INSERT INTO parent (id, tenant_id, name, phone, wechat_user_id) "
                "VALUES (:id, :t, '家长乙', '13800000002', NULL)"
            ),
            {"id": parent2_id, "t": tenant_a},
        )
        await conn.execute(
            text(
                "INSERT INTO parent_student (id, tenant_id, parent_id, student_id, relation) "
                "VALUES (:id, :t, :p, :s, '家长')"
            ),
            {"id": uuid4(), "t": tenant_a, "p": parent_id, "s": student_a1},
        )
        # ---- 班级 + 名单（ca1=teacher 任教含 a1/a2；ca2=teacher2 任教含 a3） ----
        for cid, tid_, name in ((class_a1, teacher_id, "A1班"), (class_a2, teacher2_id, "A2班")):
            await conn.execute(
                text(
                    "INSERT INTO class (id, tenant_id, course_id, teacher_id, classroom_id, "
                    "name, capacity, term_start, term_end, status) "
                    "VALUES (:id, :t, :course, :teacher, :room, :name, 20, :start, :end, 'active')"
                ),
                {
                    "id": cid,
                    "t": tenant_a,
                    "course": course_a,
                    "teacher": tid_,
                    "room": classroom_a,
                    "name": name,
                    "start": today - timedelta(days=7),
                    "end": today + timedelta(days=120),
                },
            )
        for sid, cid in ((student_a1, class_a1), (student_a2, class_a1), (student_a3, class_a2)):
            await conn.execute(
                text(
                    "INSERT INTO class_enrollment (id, tenant_id, class_id, student_id, joined_at) "
                    "VALUES (:id, :t, :c, :s, :j)"
                ),
                {"id": uuid4(), "t": tenant_a, "c": cid, "s": sid, "j": today},
            )
        # ---- 课次 ----
        await conn.execute(
            text(
                "INSERT INTO lesson (id, tenant_id, class_id, classroom_id, teacher_id, "
                "start_at, end_at, status) "
                "VALUES (:id, :t, :c, :room, :teacher, :start, :end, 'normal')"
            ),
            {
                "id": lesson_a,
                "t": tenant_a,
                "c": class_a1,
                "room": classroom_a,
                "teacher": teacher_id,
                "start": today + timedelta(days=1),
                "end": today + timedelta(days=1, hours=2),
            },
        )
        # ---- a1 购课（供 refund/adjustment 用 purchase_id）+ org_config ----
        package_id = uuid4()
        await conn.execute(
            text(
                "INSERT INTO course_package (id, tenant_id, name, total_lessons, gift_lessons, "
                "price, valid_months) VALUES (:id, :t, '10次卡', 10, 0, 1000, 6)"
            ),
            {"id": package_id, "t": tenant_a},
        )
        purchase_a = uuid4()
        await conn.execute(
            text(
                "INSERT INTO purchase (id, tenant_id, student_id, course_id, package_id, "
                "quantity, amount, expires_at) VALUES (:id, :t, :s, :c, :p, 1, 1000, NULL)"
            ),
            {"id": purchase_a, "t": tenant_a, "s": student_a1, "c": course_a, "p": package_id},
        )
        await conn.execute(
            text(
                "INSERT INTO lesson_ledger (id, tenant_id, student_id, purchase_id, lesson_id, "
                "type, delta, operator_id) VALUES (:id, :t, :s, :p, NULL, 'purchase', 10, :op)"
            ),
            {"id": uuid4(), "t": tenant_a, "s": student_a1, "p": purchase_a, "op": admin_id},
        )
        await conn.execute(
            text("INSERT INTO org_config (id, tenant_id) VALUES (:id, :t)"),
            {"id": uuid4(), "t": tenant_a},
        )
        # ---- 员工账号（租户 A） ----
        for sid, role, phone, linked in (
            (admin_id, "admin", admin_phone, None),
            (registrar_id, "registrar", registrar_phone, None),
            (teacher_staff_id, "teacher", teacher_phone, teacher_id),
        ):
            await conn.execute(
                text(
                    "INSERT INTO staff_account (id, tenant_id, name, phone, password_hash, "
                    "role, teacher_id, status) "
                    "VALUES (:id, :t, :name, :phone, :hash, :role, :t2, 'active')"
                ),
                {
                    "id": sid,
                    "t": tenant_a,
                    "name": f"{role}账号",
                    "phone": phone,
                    "hash": pw_hash,
                    "role": role,
                    "t2": linked,
                },
            )

        # ---- 租户 B（靶机） ----
        await conn.execute(
            text(
                "INSERT INTO course (id, tenant_id, name, subject, duration_min, status) "
                "VALUES (:id, :t, '物理', '物理', 90, 'active')"
            ),
            {"id": course_b, "t": tenant_b},
        )
        await conn.execute(
            text(
                "INSERT INTO classroom (id, tenant_id, name, capacity) "
                "VALUES (:id, :t, '教室B', 20)"
            ),
            {"id": classroom_b, "t": tenant_b},
        )
        await conn.execute(
            text(
                "INSERT INTO teacher (id, tenant_id, name, status) "
                "VALUES (:id, :t, '老师B', 'active')"
            ),
            {"id": teacher_b_id, "t": tenant_b},
        )
        await conn.execute(
            text(
                "INSERT INTO student (id, tenant_id, name, status) "
                "VALUES (:id, :t, '学生B', 'active')"
            ),
            {"id": student_b, "t": tenant_b},
        )
        await conn.execute(
            text(
                "INSERT INTO parent (id, tenant_id, name, phone, wechat_user_id) "
                "VALUES (:id, :t, '家长B', '13800000003', NULL)"
            ),
            {"id": parent_b_id, "t": tenant_b},
        )
        await conn.execute(
            text(
                "INSERT INTO class (id, tenant_id, course_id, teacher_id, classroom_id, "
                "name, capacity, term_start, term_end, status) "
                "VALUES (:id, :t, :course, :teacher, :room, 'B班', 20, :start, :end, 'active')"
            ),
            {
                "id": class_b,
                "t": tenant_b,
                "course": course_b,
                "teacher": teacher_b_id,
                "room": classroom_b,
                "start": today - timedelta(days=7),
                "end": today + timedelta(days=120),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO class_enrollment (id, tenant_id, class_id, student_id, joined_at) "
                "VALUES (:id, :t, :c, :s, :j)"
            ),
            {"id": uuid4(), "t": tenant_b, "c": class_b, "s": student_b, "j": today},
        )
        await conn.execute(
            text(
                "INSERT INTO lesson (id, tenant_id, class_id, classroom_id, teacher_id, "
                "start_at, end_at, status) "
                "VALUES (:id, :t, :c, :room, :teacher, :start, :end, 'normal')"
            ),
            {
                "id": lesson_b,
                "t": tenant_b,
                "c": class_b,
                "room": classroom_b,
                "teacher": teacher_b_id,
                "start": today + timedelta(days=1),
                "end": today + timedelta(days=1, hours=2),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO staff_account (id, tenant_id, name, phone, password_hash, role, "
                "status) VALUES (:id, :t, 'B管理员', :phone, :hash, 'admin', 'active')"
            ),
            {"id": staff_b_id, "t": tenant_b, "phone": admin_b_phone, "hash": pw_hash},
        )

    # ---- 登录 ----
    platform = await seed_staff(
        owner_engine,
        tenant_name=f"平台运营-{uuid4().hex[:8]}",
        role="platform_admin",
        phone=unique_phone(),
        password=PASSWORD,
    )
    platform_token = await _login(client, platform.phone)

    return MatrixSeed(
        tenant_a=tenant_a,
        tenant_b=tenant_b,
        admin_a=await _login(client, admin_phone),
        registrar_a=await _login(client, registrar_phone),
        teacher_a=await _login(client, teacher_phone),
        parent_a=await _parent_login(client, parent_openid),
        admin_b=await _login(client, admin_b_phone),
        platform=platform_token,
        course_a=course_a,
        classroom_a=classroom_a,
        teacher_id=teacher_id,
        student_a1=student_a1,
        student_a2=student_a2,
        student_a3=student_a3,
        parent_id=parent_id,
        parent2_id=parent2_id,
        class_a1=class_a1,
        class_a2=class_a2,
        lesson_a=lesson_a,
        purchase_id=purchase_a,
        student_b=student_b,
        class_b=class_b,
        lesson_b=lesson_b,
        teacher_b_id=teacher_b_id,
        classroom_b=classroom_b,
        course_b=course_b,
        staff_b_id=staff_b_id,
        parent_b_id=parent_b_id,
    )


# --------------------------------------------------------------------------- 角色闸门


async def test_office_write_gate_denied(client: httpx.AsyncClient, matrix_seed: MatrixSeed) -> None:
    """OfficeUser 写接口：teacher/parent 全 403（含改变关系的 transfer/refund/makeup/adjust）。"""

    seed = matrix_seed
    cases = [
        ("学生新建", "POST", "/students", {"name": "新学生"}),
        ("学生归档", "PATCH", f"/students/{seed.student_a1}", {"status": "archived"}),
        ("家长新建", "POST", "/parents", {"name": "新家长", "phone": "13900000010"}),
        ("老师新建", "POST", "/teachers", {"name": "新老师"}),
        ("教室新建", "POST", "/classrooms", {"name": "新教室", "capacity": 10}),
        (
            "教务直绑",
            "POST",
            "/bindings",
            {
                "parent_id": str(seed.parent2_id),
                "student_id": str(seed.student_a2),
                "relation": "家长",
            },
        ),
        ("课程新建", "POST", "/courses", {"name": "新课程"}),
        (
            "班级新建",
            "POST",
            "/classes",
            {
                "course_id": str(seed.course_a),
                "teacher_id": str(seed.teacher_id),
                "classroom_id": str(seed.classroom_a),
                "name": "新班级",
                "term_start": "2026-01-05",
                "term_end": "2026-03-05",
            },
        ),
        (
            "缴费",
            "POST",
            "/purchases",
            {
                "student_id": str(seed.student_a1),
                "course_id": str(seed.course_a),
                "lesson_count": 5,
                "valid_months": 3,
                "amount": "500.00",
            },
        ),
        (
            "课时调整",
            "POST",
            "/ledger/adjustments",
            {
                "student_id": str(seed.student_a1),
                "purchase_id": str(seed.purchase_id),
                "delta": 1,
                "reason": "矩阵测试调整",
            },
        ),
        (
            "排课规律",
            "POST",
            "/schedule-rules",
            {
                "class_id": str(seed.class_a1),
                "classroom_id": str(seed.classroom_a),
                "teacher_id": str(seed.teacher_id),
                "weekday": 0,
                "start_time": "09:00",
                "end_time": "10:00",
                "start_date": "2026-01-05",
                "weeks": 10,
            },
        ),
        ("节假日", "POST", "/tenant-holidays", {"dates": ["2026-06-01"], "name": "六一"}),
        ("单次调课", "PATCH", f"/lessons/{seed.lesson_a}", {"status": "cancelled"}),
        (
            "补课",
            "POST",
            f"/lessons/{seed.lesson_a}/make-ups",
            {
                "student_id": str(seed.student_a1),
                "target_lesson_id": str(seed.lesson_a),
            },
        ),
        (
            "退费",
            "POST",
            "/refunds",
            {
                "student_id": str(seed.student_a1),
                "purchase_id": str(seed.purchase_id),
                "refund_lessons": 1,
                "refund_amount": "100.00",
                "reason": "矩阵测试退费",
            },
        ),
        (
            "转班",
            "POST",
            "/transfers",
            {
                "student_id": str(seed.student_a1),
                "from_class_id": str(seed.class_a1),
                "to_class_id": str(seed.class_a2),
            },
        ),
        ("请假规则", "PATCH", "/org-config/absence-deduct-rule", {"absence_deduct_rule": "deduct"}),
    ]
    for name, method, path, payload in cases:
        for label, token in (("teacher", seed.teacher_a), ("parent", seed.parent_a)):
            resp = await client.request(method, path, json=payload, headers=_bearer(token))
            assert resp.status_code == 403, (
                f"{name} as {label} 应 403，实际 {resp.status_code} {resp.text}"
            )


async def test_office_write_gate_allowed(
    client: httpx.AsyncClient, matrix_seed: MatrixSeed
) -> None:
    """OfficeUser 写接口：admin/registrar 能写（用无害新建操作，避免污染归属断言）。"""

    seed = matrix_seed
    cases = [
        ("学生新建", "POST", "/students", {"name": "新学生X"}, 201),
        ("老师新建", "POST", "/teachers", {"name": "新老师X"}, 201),
        ("教室新建", "POST", "/classrooms", {"name": "新教室X", "capacity": 10}, 201),
        ("课程新建", "POST", "/courses", {"name": "新课程X"}, 201),
        (
            "班级新建",
            "POST",
            "/classes",
            {
                "course_id": str(seed.course_a),
                "teacher_id": str(seed.teacher_id),
                "classroom_id": str(seed.classroom_a),
                "name": "新班级X",
                "term_start": "2026-01-05",
                "term_end": "2026-03-05",
            },
            201,
        ),
        (
            "排课规律",
            "POST",
            "/schedule-rules",
            {
                "class_id": str(seed.class_a1),
                "classroom_id": str(seed.classroom_a),
                "teacher_id": str(seed.teacher_id),
                "weekday": 1,
                "start_time": "10:00",
                "end_time": "11:00",
                "start_date": "2026-01-05",
                "weeks": 10,
            },
            201,
        ),
        ("节假日", "POST", "/tenant-holidays", {"dates": ["2026-07-01"]}, 200),
        (
            "请假规则",
            "PATCH",
            "/org-config/absence-deduct-rule",
            {"absence_deduct_rule": "none"},
            200,
        ),
    ]
    for name, method, path, payload, ok_status in cases:
        for label, token in (("admin", seed.admin_a), ("registrar", seed.registrar_a)):
            resp = await client.request(method, path, json=payload, headers=_bearer(token))
            assert resp.status_code == ok_status, (
                f"{name} as {label}: {resp.status_code} {resp.text}"
            )
    # 家长新建：phone 有唯一约束，admin/registrar 各用独立手机号。
    for label, token in (("admin", seed.admin_a), ("registrar", seed.registrar_a)):
        resp = await client.post(
            "/parents",
            json={"name": f"新家长-{label}", "phone": unique_phone()},
            headers=_bearer(token),
        )
        assert resp.status_code == 201, f"家长新建 as {label}: {resp.status_code} {resp.text}"


async def test_office_read_gate(client: httpx.AsyncClient, matrix_seed: MatrixSeed) -> None:
    """OfficeUser 只读接口：office 200，teacher/parent 403。"""

    seed = matrix_seed
    window = {"from": "2026-01-01T00:00:00+08:00", "to": "2026-12-31T00:00:00+08:00"}
    cases = [
        ("出勤报表", "/reports/attendance-summary", window),
        ("课耗报表", "/reports/consumption", window),
        ("老师课时报表", "/reports/teacher-hours", window),
        ("排课规律列表", "/schedule-rules", None),
        ("节假日列表", "/tenant-holidays", None),
        ("课次列表", "/lessons", None),
        ("老师列表", "/teachers", None),
    ]
    for name, path, params in cases:
        for label, token in (("admin", seed.admin_a), ("registrar", seed.registrar_a)):
            resp = await client.get(path, params=params, headers=_bearer(token))
            assert resp.status_code == 200, f"{name} as {label}: {resp.status_code} {resp.text}"
        for label, token in (("teacher", seed.teacher_a), ("parent", seed.parent_a)):
            resp = await client.get(path, params=params, headers=_bearer(token))
            assert resp.status_code == 403, f"{name} as {label} 应 403，实际 {resp.status_code}"


async def test_teacher_or_office_gate(client: httpx.AsyncClient, matrix_seed: MatrixSeed) -> None:
    """TeacherOrOffice 接口（读 + 点名写 + 老师本人档案）：office+teacher 200，parent 403。"""

    seed = matrix_seed
    read_cases = [
        ("学生列表", "/students"),
        ("教室列表", "/classrooms"),
        ("班级列表", "/classes"),
        ("班级详情", f"/classes/{seed.class_a1}"),
        ("班级名单", f"/classes/{seed.class_a1}/students"),
        ("课次点名结果", f"/lessons/{seed.lesson_a}/attendance"),
        ("请假规则读", "/org-config/absence-deduct-rule"),
    ]
    for name, path in read_cases:
        for label, token in (
            ("admin", seed.admin_a),
            ("registrar", seed.registrar_a),
            ("teacher", seed.teacher_a),
        ):
            resp = await client.get(path, headers=_bearer(token))
            assert resp.status_code == 200, f"{name} as {label}: {resp.status_code} {resp.text}"
        resp = await client.get(path, headers=_bearer(seed.parent_a))
        assert resp.status_code == 403, f"{name} as parent 应 403，实际 {resp.status_code}"

    # 老师本人档案：teacher 200，其它角色 403。
    resp = await client.get("/teachers/me", headers=_bearer(seed.teacher_a))
    assert resp.status_code == 200, resp.text
    for label, token in (("admin", seed.admin_a), ("parent", seed.parent_a)):
        resp = await client.get("/teachers/me", headers=_bearer(token))
        assert resp.status_code == 403, f"/teachers/me as {label} 应 403，实际 {resp.status_code}"

    # 课堂点名（TeacherOrOffice 写，老师仅任教班课次）：parent 403。
    payload = {"records": [{"student_id": str(seed.student_a1), "status": "present"}]}
    resp = await client.post(
        f"/lessons/{seed.lesson_a}/attendance", json=payload, headers=_bearer(seed.parent_a)
    )
    assert resp.status_code == 403, f"点名 as parent 应 403，实际 {resp.status_code}"


async def test_ledger_audience_read(client: httpx.AsyncClient, matrix_seed: MatrixSeed) -> None:
    """课时账本读接口：admin/registrar/teacher/parent（绑定）200。"""

    seed = matrix_seed
    for path in (
        f"/students/{seed.student_a1}/balance",
        f"/students/{seed.student_a1}/ledger",
        f"/students/{seed.student_a1}/packages",
    ):
        for label, token in (
            ("admin", seed.admin_a),
            ("registrar", seed.registrar_a),
            ("teacher", seed.teacher_a),
            ("parent", seed.parent_a),
        ):
            resp = await client.get(path, headers=_bearer(token))
            assert resp.status_code == 200, f"{path} as {label}: {resp.status_code} {resp.text}"


async def test_admin_only_staff_gate(client: httpx.AsyncClient, matrix_seed: MatrixSeed) -> None:
    """员工账号管理：admin 2xx，registrar/teacher/parent 403。"""

    seed = matrix_seed
    payload = {
        "name": "新员工",
        "phone": unique_phone(),
        "role": "registrar",
        "password": "staff-pass-123",
    }
    resp = await client.post("/staff", json=payload, headers=_bearer(seed.admin_a))
    assert resp.status_code == 201, f"staff as admin: {resp.status_code} {resp.text}"
    for label, token in (
        ("registrar", seed.registrar_a),
        ("teacher", seed.teacher_a),
        ("parent", seed.parent_a),
    ):
        resp = await client.post("/staff", json=payload, headers=_bearer(token))
        assert resp.status_code == 403, f"staff as {label} 应 403，实际 {resp.status_code}"
    resp = await client.get("/staff", headers=_bearer(seed.registrar_a))
    assert resp.status_code == 403, f"GET /staff as registrar 应 403，实际 {resp.status_code}"


async def test_platform_admin_gate(client: httpx.AsyncClient, matrix_seed: MatrixSeed) -> None:
    """平台接口：platform_admin 200，机构角色全 403。"""

    seed = matrix_seed
    for path in ("/platform/tenants", "/platform/plans"):
        resp = await client.get(path, headers=_bearer(seed.platform))
        assert resp.status_code == 200, f"{path} as platform_admin: {resp.status_code}"
        for label, token in (
            ("admin", seed.admin_a),
            ("registrar", seed.registrar_a),
            ("teacher", seed.teacher_a),
            ("parent", seed.parent_a),
        ):
            resp = await client.get(path, headers=_bearer(token))
            assert resp.status_code == 403, f"{path} as {label} 应 403，实际 {resp.status_code}"


async def test_parent_endpoints_reject_staff(
    client: httpx.AsyncClient, matrix_seed: MatrixSeed
) -> None:
    """家长端 /me 接口：parent 200，staff 角色 403。"""

    seed = matrix_seed
    schedule_params = {"from": "2026-01-01T00:00:00+08:00", "to": "2026-01-20T00:00:00+08:00"}
    for path, params in (
        ("/me/students", None),
        ("/me/schedule", schedule_params),
        ("/me/balance", None),
        ("/me/ledger", None),
        ("/me/subscribe-templates", None),
    ):
        resp = await client.get(path, params=params, headers=_bearer(seed.parent_a))
        assert resp.status_code == 200, f"{path} as parent: {resp.status_code}"
        for label, token in (("admin", seed.admin_a), ("teacher", seed.teacher_a)):
            resp = await client.get(path, params=params, headers=_bearer(token))
            assert resp.status_code == 403, f"{path} as {label} 应 403，实际 {resp.status_code}"


# --------------------------------------------------------------------------- 租户闸门


async def test_tenant_gate_returns_404(client: httpx.AsyncClient, matrix_seed: MatrixSeed) -> None:
    """token A（租户 A）访问租户 B 资源 id → 一律 404，绝不 403/2xx。"""

    seed = matrix_seed
    cases = [
        ("学生详情", "GET", f"/students/{seed.student_b}", None),
        ("学生编辑", "PATCH", f"/students/{seed.student_b}", {"name": "改B"}),
        ("班级详情", "GET", f"/classes/{seed.class_b}", None),
        ("教室编辑", "PATCH", f"/classrooms/{seed.classroom_b}", {"name": "改B"}),
        ("老师编辑", "PATCH", f"/teachers/{seed.teacher_b_id}", {"name": "改B"}),
        ("课次点名结果", "GET", f"/lessons/{seed.lesson_b}/attendance", None),
        (
            "补课",
            "POST",
            f"/lessons/{seed.lesson_b}/make-ups",
            {"student_id": str(seed.student_a1), "target_lesson_id": str(seed.lesson_b)},
        ),
        (
            "缴费跨租户",
            "POST",
            "/purchases",
            {
                "student_id": str(seed.student_b),
                "course_id": str(seed.course_a),
                "lesson_count": 1,
                "valid_months": 1,
                "amount": "10.00",
            },
        ),
        ("流水跨租户", "GET", f"/students/{seed.student_b}/balance", None),
    ]
    for name, method, path, payload in cases:
        resp = await client.request(method, path, json=payload, headers=_bearer(seed.admin_a))
        assert resp.status_code == 404, f"{name} 跨租户应 404，实际 {resp.status_code} {resp.text}"


# --------------------------------------------------------------------------- 数据归属闸门


async def test_parent_data_ownership(client: httpx.AsyncClient, matrix_seed: MatrixSeed) -> None:
    """parent 只看绑定集合：绑定 a1 200，同租户未绑定 a2 403，跨租户 b 404。"""

    seed = matrix_seed
    for path in (
        f"/students/{seed.student_a1}",
        f"/students/{seed.student_a1}/balance",
        f"/students/{seed.student_a1}/ledger",
    ):
        resp = await client.get(path, headers=_bearer(seed.parent_a))
        assert resp.status_code == 200, f"{path} as parent 应 200，实际 {resp.status_code}"

    resp = await client.get(f"/students/{seed.student_a2}", headers=_bearer(seed.parent_a))
    assert resp.status_code == 403, f"未绑定孩子应 403，实际 {resp.status_code}"
    resp = await client.get(f"/students/{seed.student_a2}/balance", headers=_bearer(seed.parent_a))
    assert resp.status_code == 403, f"未绑定孩子余额应 403，实际 {resp.status_code}"

    resp = await client.get(f"/students/{seed.student_b}", headers=_bearer(seed.parent_a))
    assert resp.status_code == 404, f"跨租户应 404，实际 {resp.status_code}"


async def test_teacher_class_boundary(client: httpx.AsyncClient, matrix_seed: MatrixSeed) -> None:
    """teacher 只看任教班：任教班 a1/a2 200，非任教班 a3 403，跨租户 b 404。"""

    seed = matrix_seed
    for sid in (seed.student_a1, seed.student_a2):
        resp = await client.get(f"/students/{sid}", headers=_bearer(seed.teacher_a))
        assert resp.status_code == 200, f"任教班学生 {sid} 应 200，实际 {resp.status_code}"
    resp = await client.get(f"/students/{seed.student_a3}", headers=_bearer(seed.teacher_a))
    assert resp.status_code == 403, f"非任教班应 403，实际 {resp.status_code}"
    resp = await client.get(f"/students/{seed.student_b}", headers=_bearer(seed.teacher_a))
    assert resp.status_code == 404, f"跨租户应 404，实际 {resp.status_code}"


# ----------------------------------------------------------------- 矩阵 vs 实现冲突（口径 1）


@pytest.mark.xfail(
    strict=False,
    reason=(
        "冲突：02 矩阵标一期「课时包定义 增/改/删（US-205）」，实现无独立 CRUD 接口"
        "（课时包随购课快照生成，见 create_purchase）"
    ),
)
async def test_course_package_definition_crud(
    client: httpx.AsyncClient, matrix_seed: MatrixSeed
) -> None:
    """矩阵预期存在课时包定义 CRUD，实现未提供 → 记为冲突，交发令部裁决。"""

    resp = await client.post(
        "/course-packages",
        json={"name": "10次卡", "total_lessons": 10, "price": "1000.00", "valid_months": 6},
        headers=_bearer(matrix_seed.admin_a),
    )
    assert resp.status_code == 201


async def test_course_package_no_template_crud_snapshots_on_purchase(
    client: httpx.AsyncClient, matrix_seed: MatrixSeed
) -> None:
    """047 拍板（2026-11-03）：课时包无独立模板 CRUD，定义随购课内联快照生成。

    1. ``POST /course-packages`` 不存在 → 404（不是 201）。
    2. ``POST /purchases`` 把课时包定义固化成快照，流水含完整快照字段。
    """

    seed = matrix_seed

    # 1. 不存在课时包定义 CRUD 接口
    resp = await client.post(
        "/course-packages",
        json={"name": "10次卡", "total_lessons": 10, "price": "1000.00", "valid_months": 6},
        headers=_bearer(seed.admin_a),
    )
    assert resp.status_code == 404, f"课时包无独立 CRUD，应 404，实际 {resp.status_code}"

    # 2. 购课登记把课时包快照落库（student_a2 无历史余额，balance_after = 10 + 2）
    purchase = await client.post(
        "/purchases",
        json={
            "student_id": str(seed.student_a2),
            "course_id": str(seed.course_a),
            "lesson_count": 10,
            "gift_lesson_count": 2,
            "valid_months": 6,
            "amount": 1000.00,
        },
        headers=_bearer(seed.admin_a),
    )
    assert purchase.status_code == 201, purchase.text
    body = purchase.json()
    assert body["package_id"], "课时包快照应生成 package_id"
    assert body["package_name"] == "10次卡（赠2）", body["package_name"]
    assert body["balance_after"] == 12, body["balance_after"]
    entries = body["entries"]
    assert any(e["type"] == "purchase" and e["delta"] == 10 for e in entries), "应有购买流水"
    assert any(e["type"] == "grant" and e["delta"] == 2 for e in entries), "应有赠课流水"


async def test_no_direct_balance_update_endpoint(
    client: httpx.AsyncClient, matrix_seed: MatrixSeed
) -> None:
    """红线二：余额只能由流水变更，不存在直改余额接口（矩阵标 ❌）。"""

    seed = matrix_seed
    resp = await client.patch(
        f"/students/{seed.student_a1}/balance",
        json={"remaining": 999},
        headers=_bearer(seed.admin_a),
    )
    assert resp.status_code in (404, 405), f"不应存在直改余额接口，实际 {resp.status_code}"
