"""订阅到期只读降级（7.2）—— 到期判定 / 写闸门 / 续费 / 调度器跳过。

全部在真实 PostgreSQL 上跑（owner 连接跨租户）。What must never break：

* 到期判定：``today > end_date`` 才算到期（``end_date`` 当天仍可用）；到期后写 402、读 200；
* 全角色覆盖：admin/registrar/teacher/parent 写请求全 402，``/auth/login`` 照常 200；
* ``platform_admin`` 豁免：平台路由照常；
* 续费即时生效：+N 天从 ``max(today, end_date)`` 顺延（已过期机构从今天起算）；换 ``plan_id`` 生效；
* 调度器跳过到期租户：不再产生新的通知事件；已 enqueue 的 notification 照常发送。

``seed_staff`` / ``seed_family`` 已给租户补一条订阅（基础版，2099 到期），本文件只改到期日。
"""

import os
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services import notification_center as nc
from app.services import notification_events as ne
from app.services.ledger import BUSINESS_TZ
from tests.auth_support import seed_family, seed_staff, unique_openid, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run subscription tests",
    ),
]


async def set_end_date(owner_engine, tenant_id: UUID, end_date: date) -> None:
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE tenant_subscription SET end_date = :end WHERE tenant_id = :t"),
            {"end": end_date, "t": tenant_id},
        )


async def get_plan_id(owner_engine, tenant_id: UUID) -> UUID:
    async with owner_engine.connect() as connection:
        return await connection.scalar(
            text("SELECT plan_id FROM tenant_subscription WHERE tenant_id = :t"),
            {"t": tenant_id},
        )


async def login_headers(client: httpx.AsyncClient, phone: str, password: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"phone": phone, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# ---------------------------------------------------------------- 到期判定


async def test_expiry_boundary_today_usable_yesterday_blocked(client, owner_engine) -> None:
    """end_date = today 可写；end_date = today-1 写 402、读 200。"""

    admin = await seed_staff(
        owner_engine,
        tenant_name=f"边界机构-{uuid4().hex[:8]}",
        role="admin",
        phone=unique_phone(),
    )
    await set_end_date(owner_engine, admin.tenant_id, date.today())
    headers = await login_headers(client, admin.phone, admin.password)

    ok = await client.post("/students", json={"name": "小明"}, headers=headers)
    assert ok.status_code == 201, ok.text

    await set_end_date(owner_engine, admin.tenant_id, date.today() - timedelta(days=1))

    blocked = await client.post("/students", json={"name": "小红"}, headers=headers)
    assert blocked.status_code == 402
    assert blocked.json()["error"]["code"] == "SUBSCRIPTION_EXPIRED"

    read = await client.get("/students", headers=headers)
    assert read.status_code == 200


# ---------------------------------------------------------------- 全角色覆盖


async def test_all_tenant_roles_blocked_on_write(client, owner_engine) -> None:
    """到期后 admin/registrar/teacher 打写接口全 402；/auth/login 照常 200。"""

    for role in ("admin", "registrar", "teacher"):
        staff = await seed_staff(
            owner_engine,
            tenant_name=f"{role}机构-{uuid4().hex[:8]}",
            role=role,
            phone=unique_phone(),
        )
        await set_end_date(owner_engine, staff.tenant_id, date.today() - timedelta(days=1))
        headers = await login_headers(client, staff.phone, staff.password)
        blocked = await client.post("/students", json={"name": "学生"}, headers=headers)
        assert blocked.status_code == 402, f"{role} 应 402，实际 {blocked.status_code}"

    admin = await seed_staff(
        owner_engine,
        tenant_name=f"登录机构-{uuid4().hex[:8]}",
        role="admin",
        phone=unique_phone(),
    )
    await set_end_date(owner_engine, admin.tenant_id, date.today() - timedelta(days=1))
    login = await client.post(
        "/auth/login", json={"phone": admin.phone, "password": admin.password}
    )
    assert login.status_code == 200


async def test_parent_blocked_on_write(client, owner_engine) -> None:
    openid = unique_openid("parent-expired")
    family = await seed_family(owner_engine, entries=[("家长机构", "小明")], openid=openid)
    await set_end_date(owner_engine, family.tenant_ids[0], date.today() - timedelta(days=1))

    login = await client.post("/auth/wechat-login", json={"wx_code": openid})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    blocked = await client.post(
        "/me/active-student",
        json={"student_id": str(family.student_ids[0])},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert blocked.status_code == 402
    assert blocked.json()["error"]["code"] == "SUBSCRIPTION_EXPIRED"


# ---------------------------------------------------------------- platform_admin 豁免 + 续费


async def test_platform_admin_exempt_and_renew(client, owner_engine) -> None:
    platform = await seed_staff(
        owner_engine,
        tenant_name=f"平台运营-{uuid4().hex[:8]}",
        role="platform_admin",
        phone=unique_phone(),
    )
    platform_headers = await login_headers(client, platform.phone, platform.password)

    admin = await seed_staff(
        owner_engine,
        tenant_name=f"豁免机构-{uuid4().hex[:8]}",
        role="admin",
        phone=unique_phone(),
    )
    await set_end_date(owner_engine, admin.tenant_id, date.today() - timedelta(days=1))

    # platform_admin 对到期租户调 renew（写接口）不受闸门影响
    renew = await client.post(
        f"/platform/tenants/{admin.tenant_id}/renew",
        json={"days": 30},
        headers=platform_headers,
    )
    assert renew.status_code == 200, renew.text
    assert renew.json()["end_date"] == (date.today() + timedelta(days=30)).isoformat()


async def test_renew_restores_write_and_switches_plan(client, owner_engine) -> None:
    admin = await seed_staff(
        owner_engine,
        tenant_name=f"续费机构-{uuid4().hex[:8]}",
        role="admin",
        phone=unique_phone(),
    )
    plan_id = await get_plan_id(owner_engine, admin.tenant_id)
    await set_end_date(owner_engine, admin.tenant_id, date.today() - timedelta(days=3))
    headers = await login_headers(client, admin.phone, admin.password)
    assert (await client.post("/students", json={"name": "x"}, headers=headers)).status_code == 402

    platform = await seed_staff(
        owner_engine,
        tenant_name=f"平台续费-{uuid4().hex[:8]}",
        role="platform_admin",
        phone=unique_phone(),
    )
    platform_headers = await login_headers(client, platform.phone, platform.password)

    renew = await client.post(
        f"/platform/tenants/{admin.tenant_id}/renew",
        json={"days": 30},
        headers=platform_headers,
    )
    assert renew.status_code == 200, renew.text
    body = renew.json()
    # 已过期机构从今天起算（不是过期日起算，不白送天数）
    assert body["end_date"] == (date.today() + timedelta(days=30)).isoformat()
    assert body["plan_id"] == str(plan_id)
    assert body["plan_name"] == "基础版"

    # 续费即时生效：写恢复 201
    assert (await client.post("/students", json={"name": "y"}, headers=headers)).status_code == 201

    # 换套餐：带 plan_id 再续 10 天，从当前 end_date 顺延
    new_plan_id = uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO plan (id, name, max_students, max_messages, price) "
                "VALUES (:id, '高级版', 500, 5000, 99.00)"
            ),
            {"id": new_plan_id},
        )
    renew2 = await client.post(
        f"/platform/tenants/{admin.tenant_id}/renew",
        json={"days": 10, "plan_id": str(new_plan_id)},
        headers=platform_headers,
    )
    assert renew2.status_code == 200
    assert renew2.json()["plan_id"] == str(new_plan_id)
    assert renew2.json()["plan_name"] == "高级版"
    assert renew2.json()["end_date"] == (date.today() + timedelta(days=40)).isoformat()


async def test_renew_forbidden_for_tenant_roles(client, owner_engine) -> None:
    for role in ("admin", "registrar", "teacher"):
        staff = await seed_staff(
            owner_engine,
            tenant_name=f"越权{role}-{uuid4().hex[:8]}",
            role=role,
            phone=unique_phone(),
        )
        headers = await login_headers(client, staff.phone, staff.password)
        resp = await client.post(
            f"/platform/tenants/{staff.tenant_id}/renew",
            json={"days": 30},
            headers=headers,
        )
        assert resp.status_code == 403, f"{role} 应 403，实际 {resp.status_code}"


async def test_renew_plan_not_found_is_404(client, owner_engine) -> None:
    platform = await seed_staff(
        owner_engine,
        tenant_name=f"平台换套餐-{uuid4().hex[:8]}",
        role="platform_admin",
        phone=unique_phone(),
    )
    platform_headers = await login_headers(client, platform.phone, platform.password)
    admin = await seed_staff(
        owner_engine,
        tenant_name=f"换套餐机构-{uuid4().hex[:8]}",
        role="admin",
        phone=unique_phone(),
    )

    resp = await client.post(
        f"/platform/tenants/{admin.tenant_id}/renew",
        json={"days": 30, "plan_id": str(uuid4())},
        headers=platform_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PLAN_NOT_FOUND"


# ---------------------------------------------------------------- 调度器跳过 + 已 enqueue 照发


async def seed_reminder_tenant(
    owner_engine, *, tenant_name: str, end_date: date, lesson_start: datetime
) -> UUID:
    """造一个机构 + 订阅 + 班级 + 学生 + 家长 + 课次，供提醒扫描用。"""

    tenant_id = uuid4()
    teacher_id, classroom_id, course_id, class_id = uuid4(), uuid4(), uuid4(), uuid4()
    student_id, parent_id, lesson_id = uuid4(), uuid4(), uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
            {"id": tenant_id, "name": tenant_name},
        )
        plan_id = uuid4()
        await connection.execute(
            text(
                "INSERT INTO plan (id, name, max_students, max_messages, price) "
                "VALUES (:id, '测试套餐', 100, 1000, 0)"
            ),
            {"id": plan_id},
        )
        await connection.execute(
            text(
                "INSERT INTO tenant_subscription "
                "(id, tenant_id, plan_id, start_date, end_date, status) "
                "VALUES (:id, :t, :p, '2026-01-01', :end, 'active')"
            ),
            {"id": uuid4(), "t": tenant_id, "p": plan_id, "end": end_date},
        )
        await connection.execute(
            text("INSERT INTO teacher (id, tenant_id, name) VALUES (:id, :t, '老师')"),
            {"id": teacher_id, "t": tenant_id},
        )
        await connection.execute(
            text(
                "INSERT INTO classroom (id, tenant_id, name, capacity) VALUES (:id, :t, '教室', 20)"
            ),
            {"id": classroom_id, "t": tenant_id},
        )
        await connection.execute(
            text("INSERT INTO course (id, tenant_id, name) VALUES (:id, :t, '课程')"),
            {"id": course_id, "t": tenant_id},
        )
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
        await connection.execute(
            text("INSERT INTO student (id, tenant_id, name) VALUES (:id, :t, '小明')"),
            {"id": student_id, "t": tenant_id},
        )
        await connection.execute(
            text(
                "INSERT INTO class_enrollment (id, tenant_id, class_id, student_id, joined_at) "
                "VALUES (:id, :t, :c, :s, '2026-09-01')"
            ),
            {"id": uuid4(), "t": tenant_id, "c": class_id, "s": student_id},
        )
        await connection.execute(
            text("INSERT INTO parent (id, tenant_id, name, phone) VALUES (:id, :t, '家长', :p)"),
            {"id": parent_id, "t": tenant_id, "p": f"138{uuid4().int % 10**8:08d}"},
        )
        await connection.execute(
            text(
                "INSERT INTO parent_student (id, tenant_id, parent_id, student_id, relation) "
                "VALUES (:id, :t, :p, :s, '家长')"
            ),
            {"id": uuid4(), "t": tenant_id, "p": parent_id, "s": student_id},
        )
        await connection.execute(
            text(
                "INSERT INTO org_config (tenant_id, absence_deduct_rule, lesson_reminder_hours, "
                "balance_alert_threshold) VALUES (:t, 'none', '1440,60', 5)"
            ),
            {"t": tenant_id},
        )
        await connection.execute(
            text(
                "INSERT INTO lesson (id, tenant_id, class_id, classroom_id, teacher_id, "
                "start_at, end_at, status) VALUES (:id, :t, :c, :r, :th, :start, :end, 'normal')"
            ),
            {
                "id": lesson_id,
                "t": tenant_id,
                "c": class_id,
                "r": classroom_id,
                "th": teacher_id,
                "start": lesson_start,
                "end": lesson_start + timedelta(hours=1),
            },
        )
    return tenant_id


async def test_scheduler_skips_expired_tenant(client, owner_engine) -> None:
    """到期租户不产生新的提醒；未到期对照租户正常产生。"""

    start = datetime(2026, 12, 1, 18, 0, tzinfo=BUSINESS_TZ)
    expired_tid = await seed_reminder_tenant(
        owner_engine, tenant_name="到期提醒机构", end_date=date(2026, 11, 1), lesson_start=start
    )
    active_tid = await seed_reminder_tenant(
        owner_engine, tenant_name="正常提醒机构", end_date=date(2099, 12, 31), lesson_start=start
    )

    db_factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    await ne.scan_lesson_reminders(db_factory, now=start - timedelta(minutes=60))

    async with owner_engine.connect() as connection:
        expired_count = await connection.scalar(
            text("SELECT count(*) FROM notification WHERE tenant_id = :t"), {"t": expired_tid}
        )
        active_count = await connection.scalar(
            text("SELECT count(*) FROM notification WHERE tenant_id = :t"), {"t": active_tid}
        )
    assert expired_count == 0
    assert active_count == 1


async def test_enqueued_notification_still_sent_after_expiry(client, owner_engine) -> None:
    """到期前已 enqueue 的 pending notification，到期后引擎仍照常发成功（不追缴口径）。"""

    admin = await seed_staff(
        owner_engine,
        tenant_name=f"追缴机构-{uuid4().hex[:8]}",
        role="admin",
        phone=unique_phone(),
    )

    parent_id = uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO parent (id, tenant_id, name, phone) VALUES (:id, :t, '家长', :p)"),
            {"id": parent_id, "t": admin.tenant_id, "p": f"138{uuid4().int % 10**8:08d}"},
        )

    db_factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    async with db_factory() as db:
        async with db.begin():
            await nc.enqueue_notification(
                db,
                tenant_id=admin.tenant_id,
                event_type="test_event",
                dedupe_key="expiry-send-1",
                payload={
                    "parent_ids": [str(parent_id)],
                    "title": "测试",
                    "body": "正文",
                    "source_type": "test",
                    "source_id": str(uuid4()),
                },
                channels=[nc.CHANNEL_IN_APP],
            )

    await set_end_date(owner_engine, admin.tenant_id, date.today() - timedelta(days=1))
    await nc.process_pending_notifications(db_factory)

    async with db_factory() as db:
        status = await db.scalar(
            text("SELECT status FROM notification WHERE dedupe_key = 'expiry-send-1'")
        )
    assert status == nc.STATUS_SUCCEEDED


# ---------------------------------------------------------------- /me/subscription


async def test_me_subscription_days_left_and_expired(client, owner_engine) -> None:
    admin = await seed_staff(
        owner_engine,
        tenant_name=f"订阅状态机构-{uuid4().hex[:8]}",
        role="admin",
        phone=unique_phone(),
    )
    await set_end_date(owner_engine, admin.tenant_id, date.today() + timedelta(days=14))
    headers = await login_headers(client, admin.phone, admin.password)

    resp = await client.get("/me/subscription", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan_name"] == "基础版"
    assert body["end_date"] == (date.today() + timedelta(days=14)).isoformat()
    assert body["days_left"] == 14
    assert body["expired"] is False

    await set_end_date(owner_engine, admin.tenant_id, date.today() - timedelta(days=1))
    resp2 = await client.get("/me/subscription", headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["expired"] is True
    assert resp2.json()["days_left"] == -1
