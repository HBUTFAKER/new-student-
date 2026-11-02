"""账本对账入 CI（7.5 任务三）。

双租户各自独立走「购课 → 扣课 → 补课 / 退费」完整路径，然后调 ``reconcile``
服务层逻辑两口径断言：``--tenant``（tenant_filter）零差异 + 双租户互不污染；
全库口径我造的两个租户的 mismatch 必须为空。

负路径：直改 SQL 弄坏一条流水，对账必须能报出来（证明对账不是恒真）。

合成流水一律走 ``tenant_context`` / HTTP 接口限定在各自租户，不再犯 038 全局污染。
"""

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from app.core.security import hash_password
from app.db.session import AsyncSessionFactory
from app.db.tenant import tenant_context
from app.services.ledger import deduct_lessons
from scripts.reconcile_ledger import reconcile
from tests.auth_support import seed_subscription, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run reconcile tests",
    ),
]

PASSWORD = "reconcile-password"


@dataclass(frozen=True, slots=True)
class ReconcileSeed:
    tenant_a: UUID
    tenant_b: UUID
    admin_a_id: UUID
    admin_b_id: UUID
    admin_a_token: str
    admin_b_token: str
    student_a: UUID
    student_b: UUID
    course_a: UUID
    course_b: UUID
    lesson_a: UUID
    lesson_missed_a: UUID
    lesson_target_a: UUID
    lesson_b: UUID


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(client: httpx.AsyncClient, phone: str) -> str:
    response = await client.post("/auth/login", json={"phone": phone, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


async def _seed_tenant(
    connection,
    *,
    tenant_id: UUID,
    name: str,
    admin_id: UUID,
    phone: str,
    student_id: UUID,
    class_id: UUID,
    lesson_ids: list[UUID],
) -> UUID:
    """一个租户的完整最小数据：course/classroom/teacher/class/student/lesson/staff。"""

    course_id, classroom_id, teacher_id = uuid4(), uuid4(), uuid4()
    today = datetime.now(UTC)
    await connection.execute(
        text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
        {"id": tenant_id, "name": name},
    )
    await seed_subscription(connection, tenant_id)
    await connection.execute(
        text(
            "INSERT INTO course (id, tenant_id, name, subject, duration_min, status) "
            "VALUES (:id, :t, '数学', '数学', 90, 'active')"
        ),
        {"id": course_id, "t": tenant_id},
    )
    await connection.execute(
        text(
            "INSERT INTO classroom (id, tenant_id, name, capacity) VALUES (:id, :t, '一号教室', 20)"
        ),
        {"id": classroom_id, "t": tenant_id},
    )
    await connection.execute(
        text(
            "INSERT INTO teacher (id, tenant_id, name, status) VALUES (:id, :t, '老师', 'active')"
        ),
        {"id": teacher_id, "t": tenant_id},
    )
    await connection.execute(
        text(
            "INSERT INTO student (id, tenant_id, name, status) VALUES (:id, :t, '学员', 'active')"
        ),
        {"id": student_id, "t": tenant_id},
    )
    await connection.execute(
        text(
            "INSERT INTO class (id, tenant_id, course_id, teacher_id, classroom_id, "
            "name, capacity, term_start, term_end, status) "
            "VALUES (:id, :t, :course, :teacher, :room, 'A班', 20, :start, :end, 'active')"
        ),
        {
            "id": class_id,
            "t": tenant_id,
            "course": course_id,
            "teacher": teacher_id,
            "room": classroom_id,
            "start": today - timedelta(days=7),
            "end": today + timedelta(days=120),
        },
    )
    await connection.execute(
        text(
            "INSERT INTO class_enrollment (id, tenant_id, class_id, student_id, joined_at) "
            "VALUES (:id, :t, :class, :student, :joined)"
        ),
        {"id": uuid4(), "t": tenant_id, "class": class_id, "student": student_id, "joined": today},
    )
    for offset, lesson_id in enumerate(lesson_ids):
        start = today + timedelta(hours=offset * 4)
        await connection.execute(
            text(
                "INSERT INTO lesson (id, tenant_id, class_id, classroom_id, teacher_id, "
                "start_at, end_at, status) "
                "VALUES (:id, :t, :class, :room, :teacher, :start, :end, 'normal')"
            ),
            {
                "id": lesson_id,
                "t": tenant_id,
                "class": class_id,
                "room": classroom_id,
                "teacher": teacher_id,
                "start": start,
                "end": start + timedelta(hours=2),
            },
        )
    await connection.execute(
        text(
            "INSERT INTO staff_account (id, tenant_id, name, phone, password_hash, role, "
            "teacher_id, status) VALUES (:id, :t, :name, :phone, :hash, 'admin', NULL, 'active')"
        ),
        {
            "id": admin_id,
            "t": tenant_id,
            "name": "管理员",
            "phone": phone,
            "hash": hash_password(PASSWORD),
        },
    )
    return course_id


@pytest.fixture
async def reconcile_seed(client: httpx.AsyncClient, owner_engine) -> ReconcileSeed:
    tenant_a, tenant_b = uuid4(), uuid4()
    admin_a_id, admin_b_id = uuid4(), uuid4()
    student_a, student_b = uuid4(), uuid4()
    class_a, class_b = uuid4(), uuid4()
    lesson_a, lesson_missed_a, lesson_target_a = uuid4(), uuid4(), uuid4()
    lesson_b = uuid4()
    phone_a, phone_b = unique_phone(), unique_phone()

    async with owner_engine.begin() as connection:
        course_a = await _seed_tenant(
            connection,
            tenant_id=tenant_a,
            name=f"对账机构A-{uuid4().hex[:8]}",
            admin_id=admin_a_id,
            phone=phone_a,
            student_id=student_a,
            class_id=class_a,
            lesson_ids=[lesson_a, lesson_missed_a, lesson_target_a],
        )
        course_b = await _seed_tenant(
            connection,
            tenant_id=tenant_b,
            name=f"对账机构B-{uuid4().hex[:8]}",
            admin_id=admin_b_id,
            phone=phone_b,
            student_id=student_b,
            class_id=class_b,
            lesson_ids=[lesson_b],
        )

    return ReconcileSeed(
        tenant_a=tenant_a,
        tenant_b=tenant_b,
        admin_a_id=admin_a_id,
        admin_b_id=admin_b_id,
        admin_a_token=await _login(client, phone_a),
        admin_b_token=await _login(client, phone_b),
        student_a=student_a,
        student_b=student_b,
        course_a=course_a,
        course_b=course_b,
        lesson_a=lesson_a,
        lesson_missed_a=lesson_missed_a,
        lesson_target_a=lesson_target_a,
        lesson_b=lesson_b,
    )


async def _consume(
    seed: ReconcileSeed,
    *,
    tenant_id: UUID,
    student_id: UUID,
    lesson_id: UUID,
    count: int,
    operator_id: UUID,
) -> None:
    async with AsyncSessionFactory() as session, tenant_context(session, tenant_id):
        await deduct_lessons(
            session,
            tenant_id=tenant_id,
            student_id=student_id,
            lesson_id=lesson_id,
            count=count,
            operator_id=operator_id,
        )


async def _run_tenant_a_flow(client: httpx.AsyncClient, seed: ReconcileSeed) -> None:
    """租户 A：购 10 → 扣 2 → 补课 +1 → 退 1，最终余额 8，共 4 条流水。"""

    headers = _bearer(seed.admin_a_token)
    purchase = await client.post(
        "/purchases",
        json={
            "student_id": str(seed.student_a),
            "course_id": str(seed.course_a),
            "lesson_count": 10,
            "amount": "1000.00",
            "valid_months": 6,
        },
        headers=headers,
    )
    assert purchase.status_code == 201, purchase.text
    await _consume(
        seed,
        tenant_id=seed.tenant_a,
        student_id=seed.student_a,
        lesson_id=seed.lesson_a,
        count=2,
        operator_id=seed.admin_a_id,
    )
    makeup = await client.post(
        f"/lessons/{seed.lesson_missed_a}/make-ups",
        json={"student_id": str(seed.student_a), "target_lesson_id": str(seed.lesson_target_a)},
        headers=headers,
    )
    assert makeup.status_code == 201, makeup.text
    refund = await client.post(
        "/refunds",
        json={
            "student_id": str(seed.student_a),
            "purchase_id": purchase.json()["id"],
            "refund_lessons": 1,
            "refund_amount": "100.00",
            "reason": "对账退费",
        },
        headers=headers,
    )
    assert refund.status_code == 201, refund.text


async def _run_tenant_b_flow(client: httpx.AsyncClient, seed: ReconcileSeed) -> None:
    """租户 B：购 5 → 扣 1，最终余额 4，共 2 条流水。"""

    headers = _bearer(seed.admin_b_token)
    purchase = await client.post(
        "/purchases",
        json={
            "student_id": str(seed.student_b),
            "course_id": str(seed.course_b),
            "lesson_count": 5,
            "amount": "500.00",
            "valid_months": 3,
        },
        headers=headers,
    )
    assert purchase.status_code == 201, purchase.text
    await _consume(
        seed,
        tenant_id=seed.tenant_b,
        student_id=seed.student_b,
        lesson_id=seed.lesson_b,
        count=1,
        operator_id=seed.admin_b_id,
    )


async def test_two_tenants_zero_drift_and_no_cross_contamination(
    client: httpx.AsyncClient, reconcile_seed: ReconcileSeed
) -> None:
    seed = reconcile_seed
    await _run_tenant_a_flow(client, seed)
    await _run_tenant_b_flow(client, seed)

    # --tenant 口径：只对账这两个租户，零差异。
    scoped = await reconcile(tenant_filter=[str(seed.tenant_a), str(seed.tenant_b)])
    assert scoped["tenants"] == 2, scoped
    assert scoped["diff_count"] == 0, scoped["mismatches"]
    # 双租户互不污染：A 4 条、B 2 条，各自独立。
    assert scoped["entries"] == 6, scoped

    # 全库口径：遍历所有租户，我造的两个租户的 mismatch 必须为空。
    full = await reconcile()
    my_tenants = {str(seed.tenant_a), str(seed.tenant_b)}
    leaked = [m for m in full["mismatches"] if m.get("tenant_id") in my_tenants]
    assert not leaked, f"我造的租户在全库对账里出现差异：{leaked}"


async def test_reconcile_detects_tampered_ledger_row(
    client: httpx.AsyncClient, owner_engine, reconcile_seed: ReconcileSeed
) -> None:
    seed = reconcile_seed
    await _run_tenant_a_flow(client, seed)

    # 干净对账零差异（证明起点正确）。
    before = await reconcile(tenant_filter=[str(seed.tenant_a)])
    assert before["diff_count"] == 0, before["mismatches"]

    # 直改 SQL 弄坏一条 consume 流水：consume 必须带 lesson_id，置 NULL 触发 row_shape。
    async with owner_engine.begin() as connection:
        result = await connection.execute(
            text(
                "UPDATE lesson_ledger SET lesson_id = NULL "
                "WHERE tenant_id = :t AND type = 'consume'"
            ),
            {"t": seed.tenant_a},
        )
        assert result.rowcount == 1, "应恰好有一条 consume 流水被改坏"

    # 对账必须报出来（不是恒真）。
    after = await reconcile(tenant_filter=[str(seed.tenant_a)])
    assert after["diff_count"] >= 1, after
    kinds = {m["kind"] for m in after["mismatches"]}
    assert "row_shape" in kinds, f"应报 row_shape 差异，实际 {kinds}"
