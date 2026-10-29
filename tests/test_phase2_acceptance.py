"""Phase 2 acceptance: refund / transfer / make-up + 20 mixed entries (2.5).

The acceptance list says "hand-craft 20 mixed operations, the books must
balance". This file drives one student through buy -> gift -> consume (via the
service, as ordered) -> make-up -> transfer -> refund -> adjustment until the
ledger holds >= 20 rows, asserting after *every* step that:

* the balance equals ``SUM(delta)`` of the visible ledger rows;
* no package and no total ever goes negative;
* the roster/attendance side effects happened.

Then the reconciliation script must report zero drift, and the three attack
vectors from phase 1 (swap ids, forge a token, guess ids) are replayed against
``/purchases``, ``/refunds`` and ``/transfers``.
"""

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.security import decode_token, hash_password
from app.db.session import AsyncSessionFactory
from app.db.tenant import tenant_context
from app.services.ledger import deduct_lessons
from tests.auth_support import unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run acceptance tests",
    ),
]

PASSWORD = "phase2-password"
ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class PhaseSeed:
    tenant_id: UUID
    admin_id: UUID
    admin_token: str
    teacher_token: str
    other_admin_token: str
    student_id: UUID
    filler_id: UUID
    course_id: UUID
    class_a: UUID
    class_b: UUID
    class_c: UUID
    class_full: UUID
    lesson_1: UUID
    lesson_2: UUID
    lesson_3: UUID
    lesson_missed: UUID
    lesson_target: UUID
    other_tenant_id: UUID
    other_student_id: UUID
    other_class_id: UUID
    other_lesson_id: UUID
    other_purchase_id: UUID


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _insert(connection, sql: str, **params) -> None:
    await connection.execute(text(sql), params)


async def _login(client: httpx.AsyncClient, phone: str) -> str:
    response = await client.post("/auth/login", json={"phone": phone, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture
async def phase2_seed(client: httpx.AsyncClient, owner_engine) -> PhaseSeed:
    tenant_id, other_tenant_id = uuid4(), uuid4()
    admin_id, teacher_id, other_teacher_id = uuid4(), uuid4(), uuid4()
    other_admin_id = uuid4()
    course_id, math_course_id, other_course_id = uuid4(), uuid4(), uuid4()
    classroom_id, other_classroom_id = uuid4(), uuid4()
    student_id, filler_id = uuid4(), uuid4()
    other_student_id = uuid4()
    class_a, class_b, class_c, class_full = uuid4(), uuid4(), uuid4(), uuid4()
    other_class_id = uuid4()
    lesson_1, lesson_2, lesson_3 = uuid4(), uuid4(), uuid4()
    lesson_missed, lesson_target = uuid4(), uuid4()
    other_lesson_id = uuid4()
    admin_phone, teacher_phone, other_admin_phone = (
        unique_phone(),
        unique_phone(),
        unique_phone(),
    )
    today = date.today()
    password_hash = hash_password(PASSWORD)

    async with owner_engine.begin() as connection:
        for tid, name in (
            (tenant_id, f"验收机构-{uuid4().hex[:8]}"),
            (other_tenant_id, f"靶机机构-{uuid4().hex[:8]}"),
        ):
            await _insert(
                connection,
                "INSERT INTO tenant (id, name) VALUES (:id, :name)",
                id=tid,
                name=name,
            )
        for cid, tid, name, subject in (
            (course_id, tenant_id, "初中物理", "物理"),
            (math_course_id, tenant_id, "初中数学", "数学"),
            (other_course_id, other_tenant_id, "隔壁物理", "物理"),
        ):
            await _insert(
                connection,
                "INSERT INTO course (id, tenant_id, name, subject, duration_min, status) "
                "VALUES (:id, :tenant, :name, :subject, 90, 'active')",
                id=cid,
                tenant=tid,
                name=name,
                subject=subject,
            )
        for tid, name, linked in (
            (tenant_id, "验收老师", teacher_id),
            (other_tenant_id, "靶机老师", other_teacher_id),
        ):
            await _insert(
                connection,
                "INSERT INTO teacher (id, tenant_id, name, status) "
                "VALUES (:id, :tenant, :name, 'active')",
                id=linked,
                tenant=tid,
                name=name,
            )
        for rid, tid in ((classroom_id, tenant_id), (other_classroom_id, other_tenant_id)):
            await _insert(
                connection,
                "INSERT INTO classroom (id, tenant_id, name, capacity) "
                "VALUES (:id, :tenant, '一号教室', 20)",
                id=rid,
                tenant=tid,
            )
        for sid, tid, name in (
            (student_id, tenant_id, "验收学员"),
            (filler_id, tenant_id, "占位学员"),
            (other_student_id, other_tenant_id, "靶机学员"),
        ):
            await _insert(
                connection,
                "INSERT INTO student (id, tenant_id, name, status) "
                "VALUES (:id, :tenant, :name, 'active')",
                id=sid,
                tenant=tid,
                name=name,
            )
        class_rows = (
            (class_a, course_id, "物理A班", 20),
            (class_b, course_id, "物理B班", 20),
            (class_c, math_course_id, "数学C班", 20),
            (class_full, course_id, "物理满员班", 1),
            (other_class_id, other_course_id, "隔壁班", 20),
        )
        for cid, course, name, capacity in class_rows:
            tenant = other_tenant_id if cid == other_class_id else tenant_id
            await _insert(
                connection,
                "INSERT INTO class (id, tenant_id, course_id, teacher_id, classroom_id, "
                "name, capacity, term_start, term_end, status) "
                "VALUES (:id, :tenant, :course, :teacher, :room, :name, :capacity, "
                ":start, :end, 'active')",
                id=cid,
                tenant=tenant,
                course=course,
                teacher=other_teacher_id if tenant == other_tenant_id else teacher_id,
                room=other_classroom_id if tenant == other_tenant_id else classroom_id,
                name=name,
                capacity=capacity,
                start=today - timedelta(days=7),
                end=today + timedelta(days=120),
            )
        lesson_rows = (
            (lesson_1, class_a, "L1"),
            (lesson_2, class_a, "L2"),
            (lesson_3, class_a, "L3"),
            (lesson_missed, class_a, "missed"),
            (lesson_target, class_b, "target"),
        )
        for lid, cid, _label in lesson_rows:
            await _insert(
                connection,
                "INSERT INTO lesson (id, tenant_id, class_id, classroom_id, teacher_id, "
                "start_at, end_at, status) "
                "VALUES (:id, :tenant, :class, :room, :teacher, :start, :end, 'normal')",
                id=lid,
                tenant=tenant_id,
                **{"class": cid},
                room=classroom_id,
                teacher=teacher_id,
                start=datetime.now(UTC),
                end=datetime.now(UTC) + timedelta(hours=2),
            )
        await _insert(
            connection,
            "INSERT INTO lesson (id, tenant_id, class_id, classroom_id, teacher_id, "
            "start_at, end_at, status) "
            "VALUES (:id, :tenant, :class, :room, :teacher, :start, :end, 'normal')",
            id=other_lesson_id,
            tenant=other_tenant_id,
            **{"class": other_class_id},
            room=other_classroom_id,
            teacher=other_teacher_id,
            start=datetime.now(UTC),
            end=datetime.now(UTC) + timedelta(hours=2),
        )
        for sid, cid in ((student_id, class_a), (filler_id, class_full)):
            await _insert(
                connection,
                "INSERT INTO class_enrollment (id, tenant_id, class_id, student_id, joined_at) "
                "VALUES (:id, :tenant, :class, :student, :joined)",
                id=uuid4(),
                tenant=tenant_id,
                **{"class": cid},
                student=sid,
                joined=today,
            )
        for sid, tid, name, phone, role, linked in (
            (admin_id, tenant_id, "验收管理员", admin_phone, "admin", None),
            (uuid4(), tenant_id, "验收老师账号", teacher_phone, "teacher", teacher_id),
            (other_admin_id, other_tenant_id, "靶机管理员", other_admin_phone, "admin", None),
        ):
            await _insert(
                connection,
                "INSERT INTO staff_account (id, tenant_id, name, phone, password_hash, "
                "role, teacher_id, status) "
                "VALUES (:id, :tenant, :name, :phone, :hash, :role, :teacher, 'active')",
                id=sid,
                tenant=tid,
                name=name,
                phone=phone,
                hash=password_hash,
                role=role,
                teacher=linked,
            )

    admin_token = await _login(client, admin_phone)
    teacher_token = await _login(client, teacher_phone)
    other_admin_token = await _login(client, other_admin_phone)

    # The target tenant needs one real purchase for the swap-id attack vector.
    purchase = await client.post(
        "/purchases",
        json={
            "student_id": str(other_student_id),
            "course_id": str(other_course_id),
            "lesson_count": 10,
            "valid_months": 3,
            "amount": "1000.00",
        },
        headers=_bearer(other_admin_token),
    )
    assert purchase.status_code == 201, purchase.text

    return PhaseSeed(
        tenant_id=tenant_id,
        admin_id=admin_id,
        admin_token=admin_token,
        teacher_token=teacher_token,
        other_admin_token=other_admin_token,
        student_id=student_id,
        filler_id=filler_id,
        course_id=course_id,
        class_a=class_a,
        class_b=class_b,
        class_c=class_c,
        class_full=class_full,
        lesson_1=lesson_1,
        lesson_2=lesson_2,
        lesson_3=lesson_3,
        lesson_missed=lesson_missed,
        lesson_target=lesson_target,
        other_tenant_id=other_tenant_id,
        other_student_id=other_student_id,
        other_class_id=other_class_id,
        other_lesson_id=other_lesson_id,
        other_purchase_id=UUID(purchase.json()["id"]),
    )


# --------------------------------------------------------------- shared helpers


async def buy(client: httpx.AsyncClient, seed: PhaseSeed, *, lessons: int, gift: int = 0) -> dict:
    response = await client.post(
        "/purchases",
        json={
            "student_id": str(seed.student_id),
            "course_id": str(seed.course_id),
            "lesson_count": lessons,
            "gift_lesson_count": gift,
            "valid_months": 6,
            "amount": "3600.00",
        },
        headers=_bearer(seed.admin_token),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def refund(
    client: httpx.AsyncClient, seed: PhaseSeed, purchase_id: UUID, lessons: int, reason: str
) -> httpx.Response:
    return await client.post(
        "/refunds",
        json={
            "student_id": str(seed.student_id),
            "purchase_id": str(purchase_id),
            "refund_lessons": lessons,
            "refund_amount": "100.00",
            "reason": reason,
        },
        headers=_bearer(seed.admin_token),
    )


async def consume(seed: PhaseSeed, *, lesson_id: UUID, count: int) -> None:
    async with AsyncSessionFactory() as session, tenant_context(session, seed.tenant_id):
        await deduct_lessons(
            session,
            tenant_id=seed.tenant_id,
            student_id=seed.student_id,
            lesson_id=lesson_id,
            count=count,
            operator_id=seed.admin_id,
        )


async def assert_balanced(
    client: httpx.AsyncClient, seed: PhaseSeed, expected: int, *, min_entries: int | None = None
) -> dict:
    """Balance == SUM(delta), nothing negative, ledger fully visible."""

    headers = _bearer(seed.admin_token)
    balance = await client.get(f"/students/{seed.student_id}/balance", headers=headers)
    assert balance.status_code == 200, balance.text
    body = balance.json()
    assert body["total"] == expected
    for package in body["packages"]:
        assert package["remaining"] >= 0, package

    ledger = await client.get(
        f"/students/{seed.student_id}/ledger",
        params={"page_size": 100},
        headers=headers,
    )
    assert ledger.status_code == 200, ledger.text
    entries = ledger.json()
    assert sum(item["delta"] for item in entries["items"]) == expected
    assert entries["total"] == len(entries["items"])
    if min_entries is not None:
        assert entries["total"] >= min_entries
    return body


# --------------------------------------- the acceptance flow: >= 20 mixed entries


async def test_mixed_business_flow_balances_to_zero_drift(
    client: httpx.AsyncClient, owner_engine, phase2_seed: PhaseSeed
) -> None:
    seed = phase2_seed
    headers = _bearer(seed.admin_token)

    # 1. buy 30 + 2 gift -> 2 entries, balance 32.
    first = await buy(client, seed, lessons=30, gift=2)
    await assert_balanced(client, seed, 32)

    # 2. buy 10 -> 1 entry, balance 42.
    await buy(client, seed, lessons=10)
    await assert_balanced(client, seed, 42)

    # 3. eight one-lesson consumptions spread over three lessons (the
    #    service writes one row per call, so one call per lesson) -> 8 rows.
    for lesson in (
        seed.lesson_1,
        seed.lesson_1,
        seed.lesson_1,
        seed.lesson_2,
        seed.lesson_2,
        seed.lesson_2,
        seed.lesson_3,
        seed.lesson_3,
    ):
        await consume(seed, lesson_id=lesson, count=1)
    await assert_balanced(client, seed, 34)

    # 4. make-up for the missed lesson -> 1 grant entry, balance 35.
    make_up = await client.post(
        f"/lessons/{seed.lesson_missed}/make-ups",
        json={"student_id": str(seed.student_id), "target_lesson_id": str(seed.lesson_target)},
        headers=headers,
    )
    assert make_up.status_code == 201, make_up.text
    assert make_up.json()["type"] == "grant"
    assert make_up.json()["delta"] == 1
    assert make_up.json()["lesson_id"] == str(seed.lesson_missed)
    await assert_balanced(client, seed, 35)

    # 5. transfer A -> B: roster only, the ledger must not grow.
    transfer = await client.post(
        "/transfers",
        json={
            "student_id": str(seed.student_id),
            "from_class_id": str(seed.class_a),
            "to_class_id": str(seed.class_b),
        },
        headers=headers,
    )
    assert transfer.status_code == 201, transfer.text
    await assert_balanced(client, seed, 35)

    roster_a = await client.get(f"/classes/{seed.class_a}/students", headers=headers)
    roster_b = await client.get(f"/classes/{seed.class_b}/students", headers=headers)
    assert str(seed.student_id) not in [item["student_id"] for item in roster_a.json()["items"]]
    assert str(seed.student_id) in [item["student_id"] for item in roster_b.json()["items"]]

    # The make-up really seated the student on the target lesson roster.
    async with owner_engine.connect() as connection:
        seat = await connection.scalar(
            text(
                "SELECT status FROM attendance WHERE lesson_id = :lesson AND student_id = :student"
            ),
            {"lesson": seed.lesson_target, "student": seed.student_id},
        )
    assert seat == "makeup"

    # 6. buy 5 + 1 gift -> 2 entries, balance 41.
    await buy(client, seed, lessons=5, gift=1)
    await assert_balanced(client, seed, 41)

    # 7. refund 2 against the first package -> 1 entry, balance 39.
    refunded = await refund(client, seed, UUID(first["id"]), 2, "家长搬迁退费")
    assert refunded.status_code == 201, refunded.text
    assert refunded.json()["type"] == "refund"
    assert refunded.json()["delta"] == -2
    await assert_balanced(client, seed, 39)

    # 8/9. adjustments, one up one down -> 2 entries, balance 39 net.
    up = await client.post(
        "/ledger/adjustments",
        json={
            "student_id": str(seed.student_id),
            "purchase_id": str(first["id"]),
            "delta": 1,
            "reason": "活动奖励 1 课时",
        },
        headers=headers,
    )
    assert up.status_code == 201, up.text
    await assert_balanced(client, seed, 40)

    second_purchase = UUID(
        (
            await client.get(
                f"/students/{seed.student_id}/packages", params={"page_size": 50}, headers=headers
            )
        ).json()["items"][1]["purchase_id"]
    )
    down = await client.post(
        "/ledger/adjustments",
        json={
            "student_id": str(seed.student_id),
            "purchase_id": str(second_purchase),
            "delta": -1,
            "reason": "冲正多发的 1 课时",
        },
        headers=headers,
    )
    assert down.status_code == 201, down.text
    await assert_balanced(client, seed, 39)

    # 10. refund 1 against the second package -> 1 entry, balance 38.
    again = await refund(client, seed, second_purchase, 1, "课时争议退 1")
    assert again.status_code == 201, again.text
    await assert_balanced(client, seed, 38)

    # 11. buy 20 -> 1 entry, balance 58.
    await buy(client, seed, lessons=20)
    await assert_balanced(client, seed, 58)

    # 12. one more consumption after the transfer -> 20+ entries, balance 57.
    await consume(seed, lesson_id=seed.lesson_target, count=1)
    await assert_balanced(client, seed, 57, min_entries=20)

    # Reconciliation across every tenant: zero drift, warnings allowed.
    completed = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "scripts.reconcile_ledger", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["diff_count"] == 0, summary["mismatches"]
    # Pre-step-3 warnings: consumed lessons have no attendance rows yet.
    assert summary["warning_count"] >= 1
    assert all(
        item["kind"] in ("attendance_pending", "attendance_mismatch")
        for item in summary["warnings"]
    )


# ------------------------------------------------------------ business rejections


async def test_refund_rules(client: httpx.AsyncClient, phase2_seed: PhaseSeed) -> None:
    seed = phase2_seed
    purchase = await buy(client, seed, lessons=5)

    no_reason = await client.post(
        "/refunds",
        json={
            "student_id": str(seed.student_id),
            "purchase_id": purchase["id"],
            "refund_lessons": 1,
            "refund_amount": "10.00",
            "reason": "   ",
        },
        headers=_bearer(seed.admin_token),
    )
    assert no_reason.status_code == 422
    assert no_reason.json()["error"]["code"] == "REASON_REQUIRED"

    greedy = await refund(client, seed, UUID(purchase["id"]), 6, "多退")
    assert greedy.status_code == 409
    assert greedy.json()["error"]["code"] == "REFUND_EXCEEDS_PACKAGE"

    ghost = await refund(client, seed, uuid4(), 1, "退不存在的包")
    assert ghost.status_code == 404
    assert ghost.json()["error"]["code"] == "PURCHASE_NOT_FOUND"

    # Teachers cannot refund at all (0.2 matrix).
    denied = await client.post(
        "/refunds",
        json={
            "student_id": str(seed.student_id),
            "purchase_id": purchase["id"],
            "refund_lessons": 1,
            "refund_amount": "10.00",
            "reason": "老师尝试退费",
        },
        headers=_bearer(seed.teacher_token),
    )
    assert denied.status_code == 403

    # The rejected calls wrote nothing.
    await assert_balanced(client, seed, 5)


async def test_transfer_rules(client: httpx.AsyncClient, phase2_seed: PhaseSeed) -> None:
    seed = phase2_seed
    headers = _bearer(seed.admin_token)

    mismatch = await client.post(
        "/transfers",
        json={
            "student_id": str(seed.student_id),
            "from_class_id": str(seed.class_a),
            "to_class_id": str(seed.class_c),
        },
        headers=headers,
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "TRANSFER_COURSE_MISMATCH"

    full = await client.post(
        "/transfers",
        json={
            "student_id": str(seed.student_id),
            "from_class_id": str(seed.class_a),
            "to_class_id": str(seed.class_full),
        },
        headers=headers,
    )
    assert full.status_code == 409
    assert full.json()["error"]["code"] == "CLASS_FULL"

    not_enrolled = await client.post(
        "/transfers",
        json={
            "student_id": str(seed.student_id),
            "from_class_id": str(seed.class_b),
            "to_class_id": str(seed.class_a),
        },
        headers=headers,
    )
    assert not_enrolled.status_code == 404
    assert not_enrolled.json()["error"]["code"] == "ENROLLMENT_NOT_FOUND"

    ok = await client.post(
        "/transfers",
        json={
            "student_id": str(seed.student_id),
            "from_class_id": str(seed.class_a),
            "to_class_id": str(seed.class_b),
        },
        headers=headers,
    )
    assert ok.status_code == 201, ok.text

    back = await client.post(
        "/transfers",
        json={
            "student_id": str(seed.student_id),
            "from_class_id": str(seed.class_b),
            "to_class_id": str(seed.class_b),
        },
        headers=headers,
    )
    assert back.status_code == 409
    assert back.json()["error"]["code"] == "ALREADY_ENROLLED"


async def test_makeup_rules(client: httpx.AsyncClient, phase2_seed: PhaseSeed) -> None:
    seed = phase2_seed
    headers = _bearer(seed.admin_token)
    await buy(client, seed, lessons=10)

    payload = {"student_id": str(seed.student_id), "target_lesson_id": str(seed.lesson_target)}

    once = await client.post(
        f"/lessons/{seed.lesson_missed}/make-ups", json=payload, headers=headers
    )
    assert once.status_code == 201, once.text

    twice = await client.post(
        f"/lessons/{seed.lesson_missed}/make-ups", json=payload, headers=headers
    )
    assert twice.status_code == 409
    assert twice.json()["error"]["code"] == "MAKEUP_ALREADY_GRANTED"

    same = await client.post(
        f"/lessons/{seed.lesson_missed}/make-ups",
        json={"student_id": str(seed.student_id), "target_lesson_id": str(seed.lesson_missed)},
        headers=headers,
    )
    assert same.status_code == 422
    assert same.json()["error"]["code"] == "MAKEUP_TARGET_INVALID"

    stranger = await client.post(
        f"/lessons/{seed.lesson_missed}/make-ups",
        json={"student_id": str(seed.filler_id), "target_lesson_id": str(seed.lesson_target)},
        headers=headers,
    )
    assert stranger.status_code == 404
    assert stranger.json()["error"]["code"] == "ENROLLMENT_NOT_FOUND"

    ghost = await client.post(f"/lessons/{uuid4()}/make-ups", json=payload, headers=headers)
    assert ghost.status_code == 404
    assert ghost.json()["error"]["code"] == "LESSON_NOT_FOUND"


# ------------------------------------------------ the three attack vectors, again


def _forge(claims: dict[str, object], *, secret: str | None = None) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "role": "admin",
        "scope_type": "tenant",
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    } | claims
    key = secret if secret is not None else settings.jwt_secret_key
    return jwt.encode(payload, key, algorithm=settings.jwt_algorithm)


async def test_cross_tenant_attacks_on_trade_endpoints(
    client: httpx.AsyncClient, phase2_seed: PhaseSeed
) -> None:
    seed = phase2_seed
    headers = _bearer(seed.admin_token)

    # Vector 1: swap in the other tenant's ids. RLS makes every row invisible.
    swap_cases = [
        (
            "purchase",
            "/purchases",
            {
                "student_id": str(seed.other_student_id),
                "course_id": str(seed.course_id),
                "lesson_count": 10,
                "valid_months": 3,
                "amount": "100.00",
            },
        ),
        (
            "refund",
            "/refunds",
            {
                "student_id": str(seed.other_student_id),
                "purchase_id": str(seed.other_purchase_id),
                "refund_lessons": 1,
                "refund_amount": "10.00",
                "reason": "跨租户退费",
            },
        ),
        (
            "transfer_out",
            "/transfers",
            {
                "student_id": str(seed.student_id),
                "from_class_id": str(seed.other_class_id),
                "to_class_id": str(seed.class_b),
            },
        ),
        (
            "transfer_in",
            "/transfers",
            {
                "student_id": str(seed.student_id),
                "from_class_id": str(seed.class_a),
                "to_class_id": str(seed.other_class_id),
            },
        ),
        (
            "makeup",
            f"/lessons/{seed.other_lesson_id}/make-ups",
            {
                "student_id": str(seed.student_id),
                "target_lesson_id": str(seed.lesson_target),
            },
        ),
    ]
    for name, url, payload in swap_cases:
        response = await client.post(url, json=payload, headers=headers)
        assert response.status_code == 404, f"{name} leaked: {response.text}"

    # The target tenant's books were not touched by any of the attempts.
    other_balance = await client.get(
        f"/students/{seed.other_student_id}/balance", headers=_bearer(seed.other_admin_token)
    )
    assert other_balance.status_code == 200
    assert other_balance.json()["total"] == 10

    # Vector 2: forged tokens never reach the service.
    claims = decode_token(seed.admin_token, expected_type="access")
    tamper_suffix = "bbbb" if seed.admin_token.endswith("aaaa") else "aaaa"
    forged = {
        "broken_signature": seed.admin_token[:-4] + tamper_suffix,
        "wrong_secret": _forge(
            {"sub": str(claims.subject_id), "tenant_id": str(seed.tenant_id)},
            secret="attacker-secret",
        ),
        "tenant_swap": _forge(
            {"sub": str(claims.subject_id), "tenant_id": str(seed.other_tenant_id)}
        ),
        "role_escalation": _forge(
            {
                "sub": str(claims.subject_id),
                "tenant_id": str(seed.tenant_id),
                "role": "platform_admin",
            }
        ),
    }
    for name, token in forged.items():
        for url, payload in (
            ("/refunds", swap_cases[1][2]),
            ("/transfers", swap_cases[2][2]),
        ):
            response = await client.post(url, json=payload, headers=_bearer(token))
            assert response.status_code == 401, f"{name} accepted on {url}"
            assert response.json()["error"]["code"] in {"TOKEN_INVALID", "TOKEN_EXPIRED"}, name

    # Vector 3: guessed ids are just empty air.
    for guessed in [uuid4() for _ in range(6)]:
        purchase = await client.post(
            "/purchases",
            json={
                "student_id": str(guessed),
                "course_id": str(seed.course_id),
                "lesson_count": 1,
                "valid_months": 1,
                "amount": "1.00",
            },
            headers=headers,
        )
        assert purchase.status_code == 404

        refund = await client.post(
            "/refunds",
            json={
                "student_id": str(seed.student_id),
                "purchase_id": str(guessed),
                "refund_lessons": 1,
                "refund_amount": "1.00",
                "reason": "猜 ID",
            },
            headers=headers,
        )
        assert refund.status_code == 404

        transfer = await client.post(
            "/transfers",
            json={
                "student_id": str(seed.student_id),
                "from_class_id": str(guessed),
                "to_class_id": str(guessed),
            },
            headers=headers,
        )
        assert transfer.status_code == 404

        make_up = await client.post(
            f"/lessons/{guessed}/make-ups",
            json={"student_id": str(seed.student_id), "target_lesson_id": str(guessed)},
            headers=headers,
        )
        assert make_up.status_code == 404
