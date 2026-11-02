"""套餐限额（7.3）：学生数超限拦新建（402）+ 月消息量超限停外发（降级站内信）。

全部在真实 PostgreSQL 上跑（owner 连接跨租户 / 通知引擎走 owner session）。口径：

* 学生数（047 口径）：active + archived 全计，归档不腾名额（只排除 suspended）；只拦「新增」，
  编辑/转班不拦；
* 消息量：自然月 period=YYYY-MM、metric=messages；每个外发通道每成功发一条 = 1（多家长
  按实发条数）；失败不计、站内信（in_app）不占额；
* 超额行为：学生超限 402 ``PLAN_LIMIT_EXCEEDED``（整批导入整批拒，不截断半批）；消息超限
  外发通道全 ``ChannelSkipped(reason=PLAN_MESSAGE_LIMIT)``，链直达 in_app 兜底、无 exhausted。
"""

import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.models.notification_center import Notification
from app.services import notification_center as nc
from app.services.channels.sms import SmsChannel, StubSmsClient
from app.services.ledger import business_today
from tests.auth_support import seed_staff, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run plan limit tests",
    ),
]


# ---------------------------------------------------------------- helpers


async def set_plan_limits(
    owner_engine, tenant_id: UUID, *, max_students: int, max_messages: int
) -> None:
    async with owner_engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE plan SET max_students = :ms, max_messages = :mm "
                "WHERE id = (SELECT plan_id FROM tenant_subscription WHERE tenant_id = :t)"
            ),
            {"ms": max_students, "mm": max_messages, "t": tenant_id},
        )


async def login_headers(client, phone: str, password: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"phone": phone, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def seed_message_tenant(owner_engine, *, max_messages: int) -> tuple[UUID, UUID]:
    """一个机构 + 订阅（可调 max_messages）+ 一个带手机号的家长。返回 (tenant_id, parent_id)。"""

    tenant_id, parent_id = uuid4(), uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, '限额机构')"), {"id": tenant_id}
        )
        plan_id = uuid4()
        await connection.execute(
            text(
                "INSERT INTO plan (id, name, max_students, max_messages, price) "
                "VALUES (:id, '测试版', 100, :mm, 0)"
            ),
            {"id": plan_id, "mm": max_messages},
        )
        await connection.execute(
            text(
                "INSERT INTO tenant_subscription "
                "(id, tenant_id, plan_id, start_date, end_date, status) "
                "VALUES (:id, :t, :p, '2026-01-01', '2099-12-31', 'active')"
            ),
            {"id": uuid4(), "t": tenant_id, "p": plan_id},
        )
        await connection.execute(
            text(
                "INSERT INTO parent (id, tenant_id, name, phone) VALUES (:id, :t, '家长', :phone)"
            ),
            {"id": parent_id, "t": tenant_id, "phone": unique_phone()},
        )
    return tenant_id, parent_id


def sms_payload(parent_ids: list[UUID]) -> dict:
    return {
        "parent_ids": [str(pid) for pid in parent_ids],
        "title": "测试标题",
        "body": "测试正文",
        "source_type": "test",
        "source_id": str(uuid4()),
    }


async def enqueue(db_factory, *, tenant_id: UUID, dedupe_key: str, payload: dict, channels) -> None:
    async with db_factory() as db:
        async with db.begin():
            await nc.enqueue_notification(
                db,
                tenant_id=tenant_id,
                event_type="test_event",
                dedupe_key=dedupe_key,
                payload=payload,
                channels=channels,
            )


async def get_notification(db_factory, dedupe_key: str) -> Notification:
    async with db_factory() as db:
        return (
            await db.execute(select(Notification).where(Notification.dedupe_key == dedupe_key))
        ).scalar_one()


async def usage_value(owner_engine, tenant_id: UUID) -> int:
    async with owner_engine.connect() as connection:
        return int(
            await connection.scalar(
                text("SELECT value FROM usage_meter WHERE tenant_id = :t AND metric = 'messages'"),
                {"t": tenant_id},
            )
            or 0
        )


def sms_settings() -> Settings:
    return Settings(sms_provider="aliyun")


@pytest.fixture
def db_factory(owner_engine):
    return async_sessionmaker(owner_engine, expire_on_commit=False)


@pytest.fixture
def sms_stub() -> StubSmsClient:
    return StubSmsClient()


@pytest.fixture(autouse=True)
def isolated_channels():
    before = dict(nc._channels)
    yield
    nc._channels.clear()
    nc._channels.update(before)


@pytest.fixture(autouse=True)
async def clean_slate(owner_engine):
    """清出站队列 / 站内信 / 用量，避免 process 扫到别的测试的 pending 行。"""

    async with owner_engine.begin() as connection:
        await connection.execute(text("DELETE FROM in_app_message"))
        await connection.execute(text("DELETE FROM notification"))
        await connection.execute(text("DELETE FROM usage_meter"))
    yield


# ---------------------------------------------------------------- 学生限额


async def test_student_limit_archive_still_occupies_slot(client, owner_engine) -> None:
    """047 拍板：归档学生仍占名额（推翻 042「归档腾名额」），建满后归档不腾名额。"""

    admin = await seed_staff(
        owner_engine, tenant_name=f"学生限额-{uuid4().hex[:8]}", role="admin", phone=unique_phone()
    )
    await set_plan_limits(owner_engine, admin.tenant_id, max_students=3, max_messages=1000)
    headers = await login_headers(client, admin.phone, admin.password)

    created_ids: list[str] = []
    for name in ("s1", "s2", "s3"):
        resp = await client.post("/students", json={"name": name}, headers=headers)
        assert resp.status_code == 201, resp.text
        created_ids.append(resp.json()["id"])

    blocked = await client.post("/students", json={"name": "s4"}, headers=headers)
    assert blocked.status_code == 402
    assert blocked.json()["error"]["code"] == "PLAN_LIMIT_EXCEEDED"

    # 归档 1 人后仍超限：归档不再腾名额
    archived = await client.patch(
        f"/students/{created_ids[0]}", json={"status": "archived"}, headers=headers
    )
    assert archived.status_code == 200
    still_blocked = await client.post("/students", json={"name": "s5"}, headers=headers)
    assert still_blocked.status_code == 402
    assert still_blocked.json()["error"]["code"] == "PLAN_LIMIT_EXCEEDED"

    # /me/subscription 的 used 仍把归档学生计入（active + archived 全计）
    sub = await client.get("/me/subscription", headers=headers)
    assert sub.status_code == 200
    assert sub.json()["students_used"] == 3
    assert sub.json()["students_max"] == 3


async def test_bulk_import_whole_batch_rejected(client, owner_engine) -> None:
    admin = await seed_staff(
        owner_engine, tenant_name=f"导入限额-{uuid4().hex[:8]}", role="admin", phone=unique_phone()
    )
    await set_plan_limits(owner_engine, admin.tenant_id, max_students=5, max_messages=1000)
    headers = await login_headers(client, admin.phone, admin.password)

    # 剩余 5 导入 3 → 全成
    ok = await client.post(
        "/students/bulk",
        json={"items": [{"name": "a"}, {"name": "b"}, {"name": "c"}]},
        headers=headers,
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["total"] == 3

    # 剩余 2 导入 3 → 整批拒，文案带数字
    rejected = await client.post(
        "/students/bulk",
        json={"items": [{"name": "d"}, {"name": "e"}, {"name": "f"}]},
        headers=headers,
    )
    assert rejected.status_code == 402
    body = rejected.json()["error"]
    assert body["code"] == "PLAN_LIMIT_EXCEEDED"
    assert "剩余名额 2" in body["message"]
    assert "本批 3" in body["message"]

    # 整批不入库：仍只有 3 个在档学生
    listing = await client.get("/students?status=active", headers=headers)
    assert listing.json()["total"] == 3


async def test_edit_not_blocked_when_full(client, owner_engine) -> None:
    admin = await seed_staff(
        owner_engine, tenant_name=f"编辑不拦-{uuid4().hex[:8]}", role="admin", phone=unique_phone()
    )
    await set_plan_limits(owner_engine, admin.tenant_id, max_students=1, max_messages=1000)
    headers = await login_headers(client, admin.phone, admin.password)

    created = await client.post("/students", json={"name": "s1"}, headers=headers)
    assert created.status_code == 201
    student_id = created.json()["id"]

    # 满员后编辑姓名成功（只拦新增，不拦编辑）
    edited = await client.patch(f"/students/{student_id}", json={"name": "改"}, headers=headers)
    assert edited.status_code == 200
    assert edited.json()["name"] == "改"


# ---------------------------------------------------------------- 消息限额


async def test_message_limit_skips_external_to_in_app(db_factory, owner_engine, sms_stub) -> None:
    """max_messages=2：发满 2 条后，第 3 条外发全 skip、站内信兜底、无 exhausted。"""

    tenant_id, parent_id = await seed_message_tenant(owner_engine, max_messages=2)
    nc.register_channel("sms", SmsChannel(client=sms_stub, settings=sms_settings()))

    for i in range(2):
        await enqueue(
            db_factory,
            tenant_id=tenant_id,
            dedupe_key=f"ml-{i}",
            payload=sms_payload([parent_id]),
            channels=["sms", "in_app"],
        )
    await nc.process_pending_notifications(db_factory)
    assert await usage_value(owner_engine, tenant_id) == 2

    await enqueue(
        db_factory,
        tenant_id=tenant_id,
        dedupe_key="ml-over",
        payload=sms_payload([parent_id]),
        channels=["sms", "in_app"],
    )
    await nc.process_pending_notifications(db_factory)

    notification = await get_notification(db_factory, "ml-over")
    assert notification.status == nc.STATUS_SUCCEEDED
    assert notification.succeeded_channels == ["in_app"]
    assert notification.last_error == "PLAN_MESSAGE_LIMIT"
    assert len(sms_stub.calls) == 2  # 第 3 条没再调短信
    async with owner_engine.connect() as connection:
        inapp = int(
            await connection.scalar(
                text("SELECT count(*) FROM in_app_message WHERE parent_id = :p"),
                {"p": parent_id},
            )
            or 0
        )
    assert inapp == 1  # 只有第 3 条走站内信兜底；前两条短信成功即停、不落站内信


async def test_failed_send_not_counted(db_factory, owner_engine, sms_stub) -> None:
    """临时失败不计入用量（成功才计、失败不预占不退还）。"""

    tenant_id, parent_id = await seed_message_tenant(owner_engine, max_messages=1000)
    nc.register_channel("sms", SmsChannel(client=sms_stub, settings=sms_settings()))
    sms_stub.fail_next_with = RuntimeError("network blip")

    await enqueue(
        db_factory,
        tenant_id=tenant_id,
        dedupe_key="fail-1",
        payload=sms_payload([parent_id]),
        channels=["sms"],
    )
    await nc.process_pending_notifications(db_factory)

    notification = await get_notification(db_factory, "fail-1")
    assert notification.status == nc.STATUS_PENDING  # 临时失败仍在队等重试
    assert await usage_value(owner_engine, tenant_id) == 0


async def test_in_app_not_counted(db_factory, owner_engine, sms_stub) -> None:
    """站内信（in_app）不占消息额。"""

    tenant_id, parent_id = await seed_message_tenant(owner_engine, max_messages=1000)
    nc.register_channel("sms", SmsChannel(client=sms_stub, settings=sms_settings()))

    await enqueue(
        db_factory,
        tenant_id=tenant_id,
        dedupe_key="inapp-1",
        payload=sms_payload([parent_id]),
        channels=["in_app"],
    )
    await nc.process_pending_notifications(db_factory)

    notification = await get_notification(db_factory, "inapp-1")
    assert notification.status == nc.STATUS_SUCCEEDED
    assert notification.succeeded_channels == ["in_app"]
    assert await usage_value(owner_engine, tenant_id) == 0


async def test_multi_parent_counts_per_delivery(db_factory, owner_engine, sms_stub) -> None:
    """多家长一次通知 = 实发外发条数（2 家长 = 2 条）。"""

    tenant_id, parent_id = await seed_message_tenant(owner_engine, max_messages=1000)
    parent2 = uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO parent (id, tenant_id, name, phone) VALUES (:id, :t, '家长2', :phone)"
            ),
            {"id": parent2, "t": tenant_id, "phone": unique_phone()},
        )
    nc.register_channel("sms", SmsChannel(client=sms_stub, settings=sms_settings()))

    await enqueue(
        db_factory,
        tenant_id=tenant_id,
        dedupe_key="multi-1",
        payload=sms_payload([parent_id, parent2]),
        channels=["sms"],
    )
    await nc.process_pending_notifications(db_factory)

    assert await usage_value(owner_engine, tenant_id) == 2
    assert len(sms_stub.calls) == 2  # 两个家长各一条


async def test_period_reset_restores_budget(db_factory, owner_engine, sms_stub) -> None:
    """跨月重置：把用量 period 回拨到上月 → 当月额度恢复。"""

    tenant_id, parent_id = await seed_message_tenant(owner_engine, max_messages=1)
    nc.register_channel("sms", SmsChannel(client=sms_stub, settings=sms_settings()))

    await enqueue(
        db_factory,
        tenant_id=tenant_id,
        dedupe_key="pr-1",
        payload=sms_payload([parent_id]),
        channels=["sms"],
    )
    await nc.process_pending_notifications(db_factory)
    assert await usage_value(owner_engine, tenant_id) == 1

    # 第 2 条超限 → 站内信兜底
    await enqueue(
        db_factory,
        tenant_id=tenant_id,
        dedupe_key="pr-2",
        payload=sms_payload([parent_id]),
        channels=["sms", "in_app"],
    )
    await nc.process_pending_notifications(db_factory)
    assert (await get_notification(db_factory, "pr-2")).succeeded_channels == ["in_app"]

    # 用量回拨到上月 → 当月归零，恢复外发
    today = business_today()
    prev = (
        f"{today.year - 1:04d}-12"
        if today.month == 1
        else f"{today.year:04d}-{today.month - 1:02d}"
    )
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE usage_meter SET period = :p WHERE tenant_id = :t"),
            {"p": prev, "t": tenant_id},
        )

    await enqueue(
        db_factory,
        tenant_id=tenant_id,
        dedupe_key="pr-3",
        payload=sms_payload([parent_id]),
        channels=["sms"],
    )
    await nc.process_pending_notifications(db_factory)
    assert (await get_notification(db_factory, "pr-3")).succeeded_channels == ["sms"]


async def test_renew_bigger_plan_immediate(db_factory, owner_engine, sms_stub) -> None:
    """限额判定无缓存：改大 max_messages 立刻能继续外发。"""

    tenant_id, parent_id = await seed_message_tenant(owner_engine, max_messages=1)
    nc.register_channel("sms", SmsChannel(client=sms_stub, settings=sms_settings()))

    await enqueue(
        db_factory,
        tenant_id=tenant_id,
        dedupe_key="im-1",
        payload=sms_payload([parent_id]),
        channels=["sms"],
    )
    await nc.process_pending_notifications(db_factory)

    await enqueue(
        db_factory,
        tenant_id=tenant_id,
        dedupe_key="im-2",
        payload=sms_payload([parent_id]),
        channels=["sms", "in_app"],
    )
    await nc.process_pending_notifications(db_factory)
    assert (await get_notification(db_factory, "im-2")).succeeded_channels == ["in_app"]

    # 换大 plan（等价 renew 换套餐）→ 无缓存，立刻恢复
    await set_plan_limits(owner_engine, tenant_id, max_students=100, max_messages=100)
    await enqueue(
        db_factory,
        tenant_id=tenant_id,
        dedupe_key="im-3",
        payload=sms_payload([parent_id]),
        channels=["sms"],
    )
    await nc.process_pending_notifications(db_factory)
    assert (await get_notification(db_factory, "im-3")).succeeded_channels == ["sms"]


# ---------------------------------------------------------------- usage 接口 + /me/subscription


async def test_usage_endpoint_and_role_guard(client, owner_engine) -> None:
    platform = await seed_staff(
        owner_engine,
        tenant_name=f"平台用量-{uuid4().hex[:8]}",
        role="platform_admin",
        phone=unique_phone(),
    )
    platform_headers = await login_headers(client, platform.phone, platform.password)

    admin = await seed_staff(
        owner_engine, tenant_name=f"用量机构-{uuid4().hex[:8]}", role="admin", phone=unique_phone()
    )
    await set_plan_limits(owner_engine, admin.tenant_id, max_students=4, max_messages=1000)
    admin_headers = await login_headers(client, admin.phone, admin.password)
    await client.post("/students", json={"name": "x"}, headers=admin_headers)

    resp = await client.get(f"/platform/tenants/{admin.tenant_id}/usage", headers=platform_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["students_used"] == 1
    assert body["max_students"] == 4
    assert body["messages_used"] == 0
    assert body["max_messages"] == 1000
    assert body["plan_name"] == "基础版"

    # 机构内角色（admin/registrar/teacher）调平台用量接口 → 403
    for role in ("admin", "registrar", "teacher"):
        staff = await seed_staff(
            owner_engine,
            tenant_name=f"用量越权{role}-{uuid4().hex[:8]}",
            role=role,
            phone=unique_phone(),
        )
        headers = await login_headers(client, staff.phone, staff.password)
        denied = await client.get(f"/platform/tenants/{admin.tenant_id}/usage", headers=headers)
        assert denied.status_code == 403, f"{role} 应 403，实际 {denied.status_code}"


async def test_me_subscription_usage_fields(client, owner_engine) -> None:
    admin = await seed_staff(
        owner_engine, tenant_name=f"四字段-{uuid4().hex[:8]}", role="admin", phone=unique_phone()
    )
    await set_plan_limits(owner_engine, admin.tenant_id, max_students=4, max_messages=1000)
    headers = await login_headers(client, admin.phone, admin.password)
    await client.post("/students", json={"name": "x"}, headers=headers)
    await client.post("/students", json={"name": "y"}, headers=headers)

    resp = await client.get("/me/subscription", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["students_used"] == 2
    assert body["students_max"] == 4
    assert body["messages_used"] == 0
    assert body["messages_max"] == 1000
