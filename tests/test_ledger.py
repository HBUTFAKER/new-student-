"""Lesson packages, purchases and the append-only ledger (steps 2.3 / 2.4)."""

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
import pytest
from sqlalchemy import text

from app.core.errors import AppError
from app.core.security import hash_password
from app.db.session import AsyncSessionFactory
from app.db.tenant import tenant_context
from app.services.ledger import add_months, business_today, deduct_lessons
from tests.auth_support import unique_openid, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run ledger tests",
    ),
]

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "ledger-password"


@dataclass(frozen=True, slots=True)
class LedgerSeed:
    tenant_id: UUID
    other_tenant_id: UUID
    admin_id: UUID
    admin_phone: str
    teacher_phone: str
    other_admin_phone: str
    teacher_id: UUID
    course_id: UUID
    other_course_id: UUID
    student_id: UUID
    outsider_id: UUID
    foreign_student_id: UUID
    class_id: UUID
    lesson_id: UUID
    other_lesson_id: UUID


async def _insert(connection, sql: str, **params) -> None:
    await connection.execute(text(sql), params)


@pytest.fixture
async def ledger_seed(owner_engine) -> LedgerSeed:
    tenant_id, other_tenant_id = uuid4(), uuid4()
    admin_id = uuid4()
    teacher_id, other_teacher_id = uuid4(), uuid4()
    course_id, other_course_id = uuid4(), uuid4()
    classroom_id = uuid4()
    student_id, outsider_id, foreign_student_id = uuid4(), uuid4(), uuid4()
    class_id, other_class_id = uuid4(), uuid4()
    lesson_id, other_lesson_id = uuid4(), uuid4()
    admin_phone, teacher_phone, other_admin_phone = (
        unique_phone(),
        unique_phone(),
        unique_phone(),
    )
    password_hash = hash_password(PASSWORD)
    today = date.today()

    async with owner_engine.begin() as connection:
        for tid, name in (
            (tenant_id, f"账本机构-{uuid4().hex[:8]}"),
            (other_tenant_id, f"隔壁机构-{uuid4().hex[:8]}"),
        ):
            await _insert(
                connection,
                "INSERT INTO tenant (id, name) VALUES (:id, :name)",
                id=tid,
                name=name,
            )
        for tid, name in ((teacher_id, "主教老师"), (other_teacher_id, "别班老师")):
            await _insert(
                connection,
                "INSERT INTO teacher (id, tenant_id, name, status) "
                "VALUES (:id, :tenant, :name, 'active')",
                id=tid,
                tenant=tenant_id,
                name=name,
            )
        await _insert(
            connection,
            "INSERT INTO classroom (id, tenant_id, name, capacity) "
            "VALUES (:id, :tenant, :name, 20)",
            id=classroom_id,
            tenant=tenant_id,
            name="一号教室",
        )
        for cid, tid, name in (
            (course_id, tenant_id, "初三物理"),
            (other_course_id, other_tenant_id, "小学语文"),
        ):
            await _insert(
                connection,
                "INSERT INTO course (id, tenant_id, name, subject, duration_min, status) "
                "VALUES (:id, :tenant, :name, '物理', 90, 'active')",
                id=cid,
                tenant=tid,
                name=name,
            )
        for sid, tid, name in (
            (student_id, tenant_id, "王小明"),
            (outsider_id, tenant_id, "别班小李"),
            (foreign_student_id, other_tenant_id, "外机构小张"),
        ):
            await _insert(
                connection,
                "INSERT INTO student (id, tenant_id, name, status) "
                "VALUES (:id, :tenant, :name, 'active')",
                id=sid,
                tenant=tid,
                name=name,
            )
        for cid, teacher, name in (
            (class_id, teacher_id, "秋季A班"),
            (other_class_id, other_teacher_id, "秋季B班"),
        ):
            await _insert(
                connection,
                "INSERT INTO class (id, tenant_id, course_id, teacher_id, "
                "classroom_id, name, capacity, term_start, term_end, status) "
                "VALUES (:id, :tenant, :course, :teacher, :room, :name, 20, "
                ":start, :end, 'active')",
                id=cid,
                tenant=tenant_id,
                course=course_id,
                teacher=teacher,
                room=classroom_id,
                name=name,
                start=today - timedelta(days=7),
                end=today + timedelta(days=120),
            )
        await _insert(
            connection,
            "INSERT INTO class_enrollment (id, tenant_id, class_id, student_id, joined_at) "
            "VALUES (:id, :tenant, :class, :student, :joined)",
            id=uuid4(),
            tenant=tenant_id,
            **{"class": class_id},
            student=student_id,
            joined=today,
        )
        base = datetime.now(UTC)
        for offset, (lid, cid, teacher) in enumerate(
            (
                (lesson_id, class_id, teacher_id),
                (other_lesson_id, other_class_id, other_teacher_id),
            )
        ):
            start = base + timedelta(hours=offset * 4)
            await _insert(
                connection,
                "INSERT INTO lesson (id, tenant_id, class_id, classroom_id, teacher_id, "
                "start_at, end_at, status) "
                "VALUES (:id, :tenant, :class, :room, :teacher, :start, :end, 'normal')",
                id=lid,
                tenant=tenant_id,
                **{"class": cid},
                room=classroom_id,
                teacher=teacher,
                start=start,
                end=start + timedelta(hours=2),
            )
        for sid, tid, role, phone, linked in (
            (admin_id, tenant_id, "admin", admin_phone, None),
            (uuid4(), other_tenant_id, "admin", other_admin_phone, None),
            (uuid4(), tenant_id, "teacher", teacher_phone, teacher_id),
        ):
            await _insert(
                connection,
                "INSERT INTO staff_account (id, tenant_id, name, phone, "
                "password_hash, role, teacher_id, status) "
                "VALUES (:id, :tenant, :name, :phone, :hash, :role, :teacher, 'active')",
                id=sid,
                tenant=tid,
                name=f"员工-{role}",
                phone=phone,
                hash=password_hash,
                role=role,
                teacher=linked,
            )

    return LedgerSeed(
        tenant_id=tenant_id,
        other_tenant_id=other_tenant_id,
        admin_id=admin_id,
        admin_phone=admin_phone,
        teacher_phone=teacher_phone,
        other_admin_phone=other_admin_phone,
        teacher_id=teacher_id,
        course_id=course_id,
        other_course_id=other_course_id,
        student_id=student_id,
        outsider_id=outsider_id,
        foreign_student_id=foreign_student_id,
        class_id=class_id,
        lesson_id=lesson_id,
        other_lesson_id=other_lesson_id,
    )


async def login(client: httpx.AsyncClient, phone: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"phone": phone, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def buy(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    seed: LedgerSeed,
    *,
    lesson_count: int,
    gift: int = 0,
    months: int = 6,
    amount: str = "3600.00",
    student_id: UUID | None = None,
    course_id: UUID | None = None,
) -> dict:
    response = await client.post(
        "/purchases",
        json={
            "student_id": str(student_id or seed.student_id),
            "course_id": str(course_id or seed.course_id),
            "lesson_count": lesson_count,
            "gift_lesson_count": gift,
            "valid_months": months,
            "amount": amount,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def get_balance(client: httpx.AsyncClient, headers: dict[str, str], student_id: UUID) -> dict:
    response = await client.get(f"/students/{student_id}/balance", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def get_ledger(
    client: httpx.AsyncClient, headers: dict[str, str], student_id: UUID, **params
) -> dict:
    response = await client.get(f"/students/{student_id}/ledger", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def patch_purchase(owner_engine, purchase_id: str, **values) -> None:
    """Rewind expiry/purchase timestamps so ordering rules can be exercised."""

    assignments = ", ".join(f"{column} = :{column}" for column in values)
    async with owner_engine.begin() as connection:
        await connection.execute(
            text(f"UPDATE purchase SET {assignments} WHERE id = :id"),
            {"id": purchase_id, **values},
        )


async def consume(
    seed: LedgerSeed,
    *,
    count: int,
    lesson_id: UUID | None = None,
    student_id: UUID | None = None,
) -> list:
    async with AsyncSessionFactory() as session, tenant_context(session, seed.tenant_id):
        return await deduct_lessons(
            session,
            tenant_id=seed.tenant_id,
            student_id=student_id or seed.student_id,
            lesson_id=lesson_id or seed.lesson_id,
            count=count,
            operator_id=seed.admin_id,
        )


async def consume_error(
    seed: LedgerSeed,
    *,
    count: int,
    lesson_id: UUID | None = None,
    student_id: UUID | None = None,
) -> AppError:
    async with AsyncSessionFactory() as session:
        with pytest.raises(AppError) as exc:
            async with tenant_context(session, seed.tenant_id):
                await deduct_lessons(
                    session,
                    tenant_id=seed.tenant_id,
                    student_id=student_id or seed.student_id,
                    lesson_id=lesson_id or seed.lesson_id,
                    count=count,
                    operator_id=seed.admin_id,
                )
    return exc.value


# ------------------------------------------------------- purchase + balance


async def test_purchase_grants_lessons_and_balance_equals_ledger_sum(
    client, ledger_seed: LedgerSeed
) -> None:
    headers = await login(client, ledger_seed.admin_phone)
    purchase = await buy(client, headers, ledger_seed, lesson_count=20, gift=4)

    expected_expiry = add_months(business_today(), 6)
    assert purchase["expires_at"] in (
        expected_expiry.isoformat(),
        add_months(business_today() + timedelta(days=1), 6).isoformat(),
    )
    assert purchase["balance_after"] == 24
    assert {entry["type"]: entry["delta"] for entry in purchase["entries"]} == {
        "purchase": 20,
        "grant": 4,
    }
    assert {entry["purchase_id"] for entry in purchase["entries"]} == {purchase["id"]}

    await consume(ledger_seed, count=8)

    balance = await get_balance(client, headers, ledger_seed.student_id)
    assert balance["total"] == 16
    assert [item["granted"] for item in balance["packages"]] == [24]
    assert [item["remaining"] for item in balance["packages"]] == [16]
    assert [item["status"] for item in balance["packages"]] == ["active"]

    ledger = await get_ledger(client, headers, ledger_seed.student_id)
    assert ledger["total"] == 3
    assert sorted(entry["delta"] for entry in ledger["items"]) == [-8, 4, 20]
    assert sum(entry["delta"] for entry in ledger["items"]) == balance["total"]

    consume_rows = [entry for entry in ledger["items"] if entry["type"] == "consume"]
    assert len(consume_rows) == 1
    assert consume_rows[0]["delta"] == -8
    assert consume_rows[0]["purchase_id"] == purchase["id"]
    assert consume_rows[0]["lesson_id"] == str(ledger_seed.lesson_id)
    assert consume_rows[0]["class_name"] == "秋季A班"

    filtered = await get_ledger(client, headers, ledger_seed.student_id, type="consume")
    assert filtered["total"] == 1


async def test_purchase_rejects_bad_references_and_amounts(client, ledger_seed: LedgerSeed) -> None:
    headers = await login(client, ledger_seed.admin_phone)

    foreign_student = await client.post(
        "/purchases",
        json={
            "student_id": str(ledger_seed.foreign_student_id),
            "course_id": str(ledger_seed.course_id),
            "lesson_count": 10,
            "valid_months": 6,
            "amount": "100.00",
        },
        headers=headers,
    )
    assert foreign_student.status_code == 404
    assert foreign_student.json()["error"]["code"] == "STUDENT_NOT_FOUND"

    foreign_course = await client.post(
        "/purchases",
        json={
            "student_id": str(ledger_seed.student_id),
            "course_id": str(ledger_seed.other_course_id),
            "lesson_count": 10,
            "valid_months": 6,
            "amount": "100.00",
        },
        headers=headers,
    )
    assert foreign_course.status_code == 404
    assert foreign_course.json()["error"]["code"] == "COURSE_NOT_FOUND"

    zero_lessons = await client.post(
        "/purchases",
        json={
            "student_id": str(ledger_seed.student_id),
            "course_id": str(ledger_seed.course_id),
            "lesson_count": 0,
            "valid_months": 6,
            "amount": "100.00",
        },
        headers=headers,
    )
    assert zero_lessons.status_code == 422

    # 失败请求不落任何数据：学生余额仍为 0。
    assert (await get_balance(client, headers, ledger_seed.student_id))["total"] == 0


# ------------------------------------------------------------ deduction order


async def test_deduction_drains_earliest_expiry_first_and_splits_rows(
    client, ledger_seed: LedgerSeed
) -> None:
    headers = await login(client, ledger_seed.admin_phone)
    soon = await buy(client, headers, ledger_seed, lesson_count=5, months=3)
    later = await buy(client, headers, ledger_seed, lesson_count=20, months=6)
    assert soon["expires_at"] < later["expires_at"]

    entries = await consume(ledger_seed, count=8)

    assert [(str(entry.purchase_id), entry.delta) for entry in entries] == [
        (soon["id"], -5),
        (later["id"], -3),
    ]
    balance = await get_balance(client, headers, ledger_seed.student_id)
    assert balance["total"] == 17
    assert {item["purchase_id"]: item["remaining"] for item in balance["packages"]} == {
        soon["id"]: 0,
        later["id"]: 17,
    }


async def test_same_expiry_breaks_ties_by_purchased_at(
    client, owner_engine, ledger_seed: LedgerSeed
) -> None:
    headers = await login(client, ledger_seed.admin_phone)
    created_first = await buy(client, headers, ledger_seed, lesson_count=2)
    created_second = await buy(client, headers, ledger_seed, lesson_count=5)
    assert created_first["expires_at"] == created_second["expires_at"]

    now = datetime.now(UTC)
    # 后建的包改成"更早购买"，先购先扣必须选它而不是按创建顺序。
    await patch_purchase(owner_engine, created_first["id"], purchased_at=now - timedelta(hours=1))
    await patch_purchase(owner_engine, created_second["id"], purchased_at=now - timedelta(hours=2))

    entries = await consume(ledger_seed, count=3)

    assert len(entries) == 1
    assert str(entries[0].purchase_id) == created_second["id"]
    assert entries[0].delta == -3


async def test_expired_package_is_skipped_but_kept_on_the_books(
    client, owner_engine, ledger_seed: LedgerSeed
) -> None:
    headers = await login(client, ledger_seed.admin_phone)
    stale = await buy(client, headers, ledger_seed, lesson_count=6, months=3)
    fresh = await buy(client, headers, ledger_seed, lesson_count=4, months=6)
    await patch_purchase(
        owner_engine,
        stale["id"],
        expires_at=business_today() - timedelta(days=1),
    )

    entries = await consume(ledger_seed, count=2)
    assert [(str(entry.purchase_id), entry.delta) for entry in entries] == [(fresh["id"], -2)]

    balance = await get_balance(client, headers, ledger_seed.student_id)
    assert balance["total"] == 8
    status = {
        item["purchase_id"]: (item["status"], item["remaining"]) for item in balance["packages"]
    }
    assert status == {stale["id"]: ("expired", 6), fresh["id"]: ("active", 2)}

    # 过期包的流水还在，历史可查，且没有被扣过。
    ledger = await get_ledger(client, headers, ledger_seed.student_id)
    by_purchase: dict[str, list[int]] = {}
    for entry in ledger["items"]:
        by_purchase.setdefault(entry["purchase_id"], []).append(entry["delta"])
    assert sum(by_purchase[stale["id"]]) == 6
    assert sum(by_purchase[fresh["id"]]) == 2


async def test_insufficient_balance_writes_nothing(client, ledger_seed: LedgerSeed) -> None:
    headers = await login(client, ledger_seed.admin_phone)
    await buy(client, headers, ledger_seed, lesson_count=2)

    error = await consume_error(ledger_seed, count=3)
    assert (error.status_code, error.code) == (409, "BALANCE_EXHAUSTED")

    balance = await get_balance(client, headers, ledger_seed.student_id)
    assert balance["total"] == 2
    ledger = await get_ledger(client, headers, ledger_seed.student_id)
    assert ledger["total"] == 1
    assert ledger["items"][0]["type"] == "purchase"


async def test_concurrent_deductions_never_overspend(client, ledger_seed: LedgerSeed) -> None:
    headers = await login(client, ledger_seed.admin_phone)
    await buy(client, headers, ledger_seed, lesson_count=10)

    async def attempt() -> str:
        try:
            await consume(ledger_seed, count=2)
        except AppError as exc:
            return exc.code
        return "ok"

    results = await asyncio.gather(*(attempt() for _ in range(10)))

    assert results.count("ok") == 5, results
    assert set(results) - {"ok"} == {"BALANCE_EXHAUSTED"}

    balance = await get_balance(client, headers, ledger_seed.student_id)
    assert balance["total"] == 0

    consume_rows = await get_ledger(
        client, headers, ledger_seed.student_id, type="consume", page_size=100
    )
    assert consume_rows["total"] == 5
    assert {entry["delta"] for entry in consume_rows["items"]} == {-2}


# --------------------------------------------------------------- adjustment


async def test_adjustment_needs_reason_and_cannot_go_negative(
    client, ledger_seed: LedgerSeed
) -> None:
    headers = await login(client, ledger_seed.admin_phone)
    purchase = await buy(client, headers, ledger_seed, lesson_count=6)

    body = {
        "student_id": str(ledger_seed.student_id),
        "purchase_id": purchase["id"],
        "delta": -1,
    }
    missing = await client.post("/ledger/adjustments", json=body, headers=headers)
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "REASON_REQUIRED"

    blank = await client.post(
        "/ledger/adjustments", json={**body, "reason": "   "}, headers=headers
    )
    assert blank.status_code == 422
    assert blank.json()["error"]["code"] == "REASON_REQUIRED"

    negative = await client.post(
        "/ledger/adjustments",
        json={**body, "delta": -10, "reason": "多退"},
        headers=headers,
    )
    assert negative.status_code == 409
    assert negative.json()["error"]["code"] == "INSUFFICIENT_BALANCE"

    adjusted = await client.post(
        "/ledger/adjustments",
        json={**body, "delta": -2, "reason": "投诉退赔 2 课时"},
        headers=headers,
    )
    assert adjusted.status_code == 201, adjusted.text
    assert adjusted.json()["type"] == "adjust"
    assert adjusted.json()["delta"] == -2
    assert adjusted.json()["reason"] == "投诉退赔 2 课时"
    assert adjusted.json()["operator_id"] == str(ledger_seed.admin_id)

    balance = await get_balance(client, headers, ledger_seed.student_id)
    assert balance["total"] == 4
    assert balance["packages"][0]["remaining"] == 4

    ledger = await get_ledger(client, headers, ledger_seed.student_id)
    assert sorted(entry["type"] for entry in ledger["items"]) == ["adjust", "purchase"]


# ------------------------------------------------------- tenant / role limits


async def test_cross_tenant_purchase_and_adjust_are_404(client, ledger_seed: LedgerSeed) -> None:
    a_headers = await login(client, ledger_seed.admin_phone)
    b_headers = await login(client, ledger_seed.other_admin_phone)
    b_purchase = await buy(
        client,
        b_headers,
        ledger_seed,
        lesson_count=4,
        student_id=ledger_seed.foreign_student_id,
        course_id=ledger_seed.other_course_id,
    )

    assert (
        await client.get(f"/students/{ledger_seed.foreign_student_id}/balance", headers=a_headers)
    ).status_code == 404
    assert (
        await client.get(f"/students/{ledger_seed.foreign_student_id}/ledger", headers=a_headers)
    ).status_code == 404

    adjustment = await client.post(
        "/ledger/adjustments",
        json={
            "student_id": str(ledger_seed.foreign_student_id),
            "purchase_id": b_purchase["id"],
            "delta": -1,
            "reason": "越权调整",
        },
        headers=a_headers,
    )
    assert adjustment.status_code == 404
    assert adjustment.json()["error"]["code"] == "STUDENT_NOT_FOUND"

    b_balance = await get_balance(client, b_headers, ledger_seed.foreign_student_id)
    assert b_balance["total"] == 4


async def test_teacher_sees_only_own_class_consumption(client, ledger_seed: LedgerSeed) -> None:
    admin = await login(client, ledger_seed.admin_phone)
    teacher = await login(client, ledger_seed.teacher_phone)
    await buy(client, admin, ledger_seed, lesson_count=10)

    await consume(ledger_seed, count=3)
    await consume(ledger_seed, count=2, lesson_id=ledger_seed.other_lesson_id)

    own = await get_ledger(client, teacher, ledger_seed.student_id)
    assert own["total"] == 2
    assert [entry for entry in own["items"] if entry["type"] == "consume"][0]["delta"] == -3

    # 管理员看全部 3 条：购买 + 本班 consume + 别班 consume
    full = await get_ledger(client, admin, ledger_seed.student_id)
    assert full["total"] == 3

    outsider = await client.get(f"/students/{ledger_seed.outsider_id}/balance", headers=teacher)
    assert outsider.status_code == 403
    assert outsider.json()["error"]["code"] == "OUT_OF_SCOPE"


async def test_parent_reads_only_bound_child(client, ledger_seed: LedgerSeed) -> None:
    admin = await login(client, ledger_seed.admin_phone)
    await buy(client, admin, ledger_seed, lesson_count=5)
    await consume(ledger_seed, count=1)

    invite = await client.post(
        "/invite-codes",
        json={"student_id": str(ledger_seed.student_id)},
        headers=admin,
    )
    assert invite.status_code == 201, invite.text
    openid = unique_openid("ledger-parent")
    wechat = await client.post("/auth/wechat-login", json={"wx_code": openid})
    assert wechat.status_code == 200, wechat.text
    bound = await client.post(
        "/me/bindings",
        json={"invite_code": invite.json()["code"]},
        headers={"Authorization": f"Bearer {wechat.json()['access_token']}"},
    )
    assert bound.status_code == 201, bound.text
    parent = {"Authorization": f"Bearer {bound.json()['access_token']}"}

    balance = await get_balance(client, parent, ledger_seed.student_id)
    assert balance["total"] == 4
    ledger = await get_ledger(client, parent, ledger_seed.student_id)
    assert ledger["total"] == 2

    other_child = await client.get(f"/students/{ledger_seed.outsider_id}/balance", headers=parent)
    assert other_child.status_code == 403
    assert other_child.json()["error"]["code"] == "OUT_OF_SCOPE"

    unbound = await client.post(
        "/auth/wechat-login", json={"wx_code": unique_openid("ledger-unbound")}
    )
    stranger = {"Authorization": f"Bearer {unbound.json()['access_token']}"}
    blocked = await client.get(f"/students/{ledger_seed.student_id}/balance", headers=stranger)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] in {"OUT_OF_SCOPE", "TENANT_CONTEXT_REQUIRED"}


# ------------------------------------------------------------- reconciliation


async def test_reconcile_script_reports_zero_drift(
    client, owner_engine, ledger_seed: LedgerSeed
) -> None:
    headers = await login(client, ledger_seed.admin_phone)
    purchase = await buy(client, headers, ledger_seed, lesson_count=8, gift=2)
    await consume(ledger_seed, count=3)
    await client.post(
        "/ledger/adjustments",
        json={
            "student_id": str(ledger_seed.student_id),
            "purchase_id": purchase["id"],
            "delta": 1,
            "reason": "补偿 1 课时",
        },
        headers=headers,
    )

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
    payload = json.loads(completed.stdout[completed.stdout.index("{") :])
    assert payload["diff_count"] == 0
    assert payload["mismatches"] == []
    assert payload["entries"] >= 3
    assert payload["students"] >= 1
