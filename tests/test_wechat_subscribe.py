"""微信订阅消息通道（6.2）—— 额度扣减 / 降级 / 重试 / 并发原子性。

全部在真实 PostgreSQL 上跑，stub 客户端注入（不走真微信）。What must never break：

* one_time 发一条扣 1 额度，扣到 0 就降级（写站内信、不抛异常、通道算成功）；
* 从没授权过 → 降级；long_term 有 authorized 授权即可连发、额度不动；
* 临时失败 → 原子退还额度 + 抛异常让 6.1 重试；永久失败（43101 等）→ 退还额度 + 降级不重试；
* 并发扣减：count=1 时两个发送并发，恰好一个扣到发出、另一个降级（``WHERE available_count > 0``
  的原子性，不是先查后写）。
"""

import asyncio
import os
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.notification_center import Notification
from app.services import notification_center as nc
from app.services.channels.wechat_subscribe import (
    StubWechatSubscribeClient,
    WechatSubscribeChannel,
)
from tests.auth_support import unique_openid, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run wechat subscribe tests",
    ),
]

# 与 config._default_subscribe_templates 对齐。
REMIND = "stub_lesson_remind"  # one_time，scene=lesson_remind
CANCELLED = "stub_lesson_cancelled"  # long_term，scene=lesson_cancelled
APP_ID = "wx_platform_stub"


@dataclass(frozen=True, slots=True)
class Seed:
    tenant_id: UUID
    parent_id: UUID
    wechat_user_id: UUID
    openid: str


@pytest.fixture
def db_factory(owner_engine):
    return async_sessionmaker(owner_engine, expire_on_commit=False)


@pytest.fixture
def stub() -> StubWechatSubscribeClient:
    return StubWechatSubscribeClient()


@pytest.fixture(autouse=True)
def scripted_wechat_channel(stub: StubWechatSubscribeClient):
    """每个测试注入脚本化 stub，测后恢复通道注册表。"""

    before = dict(nc._channels)
    nc.register_channel("wechat_subscribe", WechatSubscribeChannel(client=stub))
    yield
    nc._channels.clear()
    nc._channels.update(before)


@pytest.fixture(autouse=True)
async def clean_slate(owner_engine):
    """清空出站队列与站内信，避免 process 扫到别的测试的 pending 行。"""

    async with owner_engine.begin() as connection:
        await connection.execute(text("DELETE FROM in_app_message"))
        await connection.execute(text("DELETE FROM notification"))
        await connection.execute(text("DELETE FROM subscribe_auth"))
    yield


@pytest.fixture
async def seed(owner_engine) -> Seed:
    """一个机构 + 一个微信身份（带 openid）+ 一个家长。"""

    tenant_id, parent_id, wechat_user_id = uuid4(), uuid4(), uuid4()
    openid = unique_openid("wsub")
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, '订阅通道机构')"), {"id": tenant_id}
        )
        await connection.execute(
            text("INSERT INTO wechat_user (id) VALUES (:id)"), {"id": wechat_user_id}
        )
        await connection.execute(
            text(
                "INSERT INTO wechat_openid (id, wechat_user_id, app_id, openid) "
                "VALUES (:id, :u, :app, :openid)"
            ),
            {"id": uuid4(), "u": wechat_user_id, "app": APP_ID, "openid": openid},
        )
        await connection.execute(
            text(
                "INSERT INTO parent (id, tenant_id, name, phone, wechat_user_id) "
                "VALUES (:id, :t, '家长', :phone, :u)"
            ),
            {"id": parent_id, "t": tenant_id, "phone": unique_phone(), "u": wechat_user_id},
        )
    return Seed(
        tenant_id=tenant_id, parent_id=parent_id, wechat_user_id=wechat_user_id, openid=openid
    )


async def set_auth(
    engine, *, tenant_id: UUID, wechat_user_id: UUID, template_id: str, status: str, count: int
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO subscribe_auth "
                "(id, tenant_id, wechat_user_id, template_id, status, available_count, updated_at) "
                "VALUES (gen_random_uuid(), :t, :u, :tpl, :status, :count, now())"
            ),
            {
                "t": tenant_id,
                "u": wechat_user_id,
                "tpl": template_id,
                "status": status,
                "count": count,
            },
        )


async def get_count(engine, *, tenant_id: UUID, wechat_user_id: UUID, template_id: str) -> int:
    async with engine.connect() as connection:
        return int(
            await connection.scalar(
                text(
                    "SELECT available_count FROM subscribe_auth "
                    "WHERE tenant_id = :t AND wechat_user_id = :u AND template_id = :tpl"
                ),
                {"t": tenant_id, "u": wechat_user_id, "tpl": template_id},
            )
            or 0
        )


async def count_inapp(engine, *, parent_id: UUID) -> int:
    async with engine.connect() as connection:
        return int(
            await connection.scalar(
                text("SELECT count(*) FROM in_app_message WHERE parent_id = :p"), {"p": parent_id}
            )
            or 0
        )


def wsub_payload(parent_id: UUID, *, scene: str) -> dict:
    return {
        "parent_ids": [str(parent_id)],
        "template_scene": scene,
        "title": "测试标题",
        "body": "测试正文",
        "source_type": "test",
        "source_id": str(uuid4()),
    }


async def enqueue(db_factory, *, tenant_id: UUID, dedupe_key: str, payload: dict) -> None:
    async with db_factory() as db:
        async with db.begin():
            await nc.enqueue_notification(
                db,
                tenant_id=tenant_id,
                event_type="test_event",
                dedupe_key=dedupe_key,
                payload=payload,
                channels=["wechat_subscribe"],
            )


async def get_notification(db_factory, dedupe_key: str) -> Notification:
    async with db_factory() as db:
        return (
            await db.execute(select(Notification).where(Notification.dedupe_key == dedupe_key))
        ).scalar_one()


async def test_one_time_quota_decremented(db_factory, owner_engine, seed, stub) -> None:
    """one_time 发一条扣 1，再发一条扣到 0；stub 各收到一次调用。"""

    await set_auth(
        owner_engine,
        tenant_id=seed.tenant_id,
        wechat_user_id=seed.wechat_user_id,
        template_id=REMIND,
        status="authorized",
        count=2,
    )
    await enqueue(
        db_factory,
        tenant_id=seed.tenant_id,
        dedupe_key="q-1",
        payload=wsub_payload(seed.parent_id, scene="lesson_remind"),
    )
    await nc.process_pending_notifications(db_factory)
    assert (
        await get_count(
            owner_engine,
            tenant_id=seed.tenant_id,
            wechat_user_id=seed.wechat_user_id,
            template_id=REMIND,
        )
        == 1
    )
    assert len(stub.calls) == 1

    await enqueue(
        db_factory,
        tenant_id=seed.tenant_id,
        dedupe_key="q-2",
        payload=wsub_payload(seed.parent_id, scene="lesson_remind"),
    )
    await nc.process_pending_notifications(db_factory)
    assert (
        await get_count(
            owner_engine,
            tenant_id=seed.tenant_id,
            wechat_user_id=seed.wechat_user_id,
            template_id=REMIND,
        )
        == 0
    )
    assert len(stub.calls) == 2


async def test_quota_exhausted_degrades(db_factory, owner_engine, seed, stub) -> None:
    """count=0 → 不抛异常、通道算成功、站内信落一条、stub 没收到调用。"""

    await set_auth(
        owner_engine,
        tenant_id=seed.tenant_id,
        wechat_user_id=seed.wechat_user_id,
        template_id=REMIND,
        status="authorized",
        count=0,
    )
    await enqueue(
        db_factory,
        tenant_id=seed.tenant_id,
        dedupe_key="deg-1",
        payload=wsub_payload(seed.parent_id, scene="lesson_remind"),
    )
    await nc.process_pending_notifications(db_factory)

    notification = await get_notification(db_factory, "deg-1")
    assert notification.status == nc.STATUS_SUCCEEDED
    assert len(stub.calls) == 0
    assert await count_inapp(owner_engine, parent_id=seed.parent_id) == 1


async def test_never_authorized_degrades(db_factory, owner_engine, seed, stub) -> None:
    """从没授权过的模板 → 降级（不落 subscribe_auth、stub 不收）。"""

    await enqueue(
        db_factory,
        tenant_id=seed.tenant_id,
        dedupe_key="deg-2",
        payload=wsub_payload(seed.parent_id, scene="lesson_remind"),
    )
    await nc.process_pending_notifications(db_factory)

    assert (await get_notification(db_factory, "deg-2")).status == nc.STATUS_SUCCEEDED
    assert len(stub.calls) == 0
    assert await count_inapp(owner_engine, parent_id=seed.parent_id) == 1


async def test_long_term_sends_without_consuming_quota(
    db_factory, owner_engine, seed, stub
) -> None:
    """long_term：授权后连发 3 条，额度字段不动、3 条都发出。"""

    await set_auth(
        owner_engine,
        tenant_id=seed.tenant_id,
        wechat_user_id=seed.wechat_user_id,
        template_id=CANCELLED,
        status="authorized",
        count=0,
    )
    for i in range(3):
        await enqueue(
            db_factory,
            tenant_id=seed.tenant_id,
            dedupe_key=f"lt-{i}",
            payload=wsub_payload(seed.parent_id, scene="lesson_cancelled"),
        )
        await nc.process_pending_notifications(db_factory)

    assert (
        await get_count(
            owner_engine,
            tenant_id=seed.tenant_id,
            wechat_user_id=seed.wechat_user_id,
            template_id=CANCELLED,
        )
        == 0
    )
    assert len(stub.calls) == 3


async def test_transient_failure_refunds_and_retries(db_factory, owner_engine, seed, stub) -> None:
    """stub 抛临时异常 → 退还额度、通知 pending；修好后重试 → 发出、再扣 1。"""

    await set_auth(
        owner_engine,
        tenant_id=seed.tenant_id,
        wechat_user_id=seed.wechat_user_id,
        template_id=REMIND,
        status="authorized",
        count=1,
    )
    stub.fail_next_with = RuntimeError("network blip")
    await enqueue(
        db_factory,
        tenant_id=seed.tenant_id,
        dedupe_key="retry-1",
        payload=wsub_payload(seed.parent_id, scene="lesson_remind"),
    )
    await nc.process_pending_notifications(db_factory)

    notification = await get_notification(db_factory, "retry-1")
    assert notification.status == nc.STATUS_PENDING
    assert (
        await get_count(
            owner_engine,
            tenant_id=seed.tenant_id,
            wechat_user_id=seed.wechat_user_id,
            template_id=REMIND,
        )
        == 1
    )  # 已退还

    # 推进到可重试
    async with db_factory() as db:
        async with db.begin():
            await db.execute(
                text(
                    "UPDATE notification SET next_retry_at = now() - interval '1 minute' "
                    "WHERE dedupe_key = 'retry-1'"
                )
            )
    await nc.process_pending_notifications(db_factory)

    notification = await get_notification(db_factory, "retry-1")
    assert notification.status == nc.STATUS_SUCCEEDED
    assert (
        await get_count(
            owner_engine,
            tenant_id=seed.tenant_id,
            wechat_user_id=seed.wechat_user_id,
            template_id=REMIND,
        )
        == 0
    )
    assert len(stub.calls) == 2


async def test_permanent_failure_degrades_without_retry(
    db_factory, owner_engine, seed, stub
) -> None:
    """stub 返回 43101 → 退还额度、降级写站内信、通道算成功、不重试。"""

    await set_auth(
        owner_engine,
        tenant_id=seed.tenant_id,
        wechat_user_id=seed.wechat_user_id,
        template_id=REMIND,
        status="authorized",
        count=1,
    )
    stub.reject_next_with = 43101
    await enqueue(
        db_factory,
        tenant_id=seed.tenant_id,
        dedupe_key="perm-1",
        payload=wsub_payload(seed.parent_id, scene="lesson_remind"),
    )
    await nc.process_pending_notifications(db_factory)

    notification = await get_notification(db_factory, "perm-1")
    assert notification.status == nc.STATUS_SUCCEEDED
    assert notification.attempts == 0  # 没进重试
    assert (
        await get_count(
            owner_engine,
            tenant_id=seed.tenant_id,
            wechat_user_id=seed.wechat_user_id,
            template_id=REMIND,
        )
        == 1
    )  # 已退还
    assert await count_inapp(owner_engine, parent_id=seed.parent_id) == 1
    assert len(stub.calls) == 1


async def test_concurrent_decrement_exactly_one_wins(db_factory, owner_engine, seed, stub) -> None:
    """count=1 时两条通知并发发送：恰好一个扣到发出、另一个降级。"""

    await set_auth(
        owner_engine,
        tenant_id=seed.tenant_id,
        wechat_user_id=seed.wechat_user_id,
        template_id=REMIND,
        status="authorized",
        count=1,
    )
    for i in range(2):
        await enqueue(
            db_factory,
            tenant_id=seed.tenant_id,
            dedupe_key=f"conc-{i}",
            payload=wsub_payload(seed.parent_id, scene="lesson_remind"),
        )

    results = await asyncio.gather(
        nc.process_pending_notifications(db_factory),
        nc.process_pending_notifications(db_factory),
    )
    assert sum(results) == 2  # 两条都被处理

    assert (
        await get_count(
            owner_engine,
            tenant_id=seed.tenant_id,
            wechat_user_id=seed.wechat_user_id,
            template_id=REMIND,
        )
        == 0
    )
    assert len(stub.calls) == 1  # 恰好一个扣到发出
    assert await count_inapp(owner_engine, parent_id=seed.parent_id) == 1  # 另一个降级


async def test_missing_openid_degrades(db_factory, owner_engine, seed, stub) -> None:
    """家长没绑 openid（或 openid 不是本 app）→ 降级，stub 不收。"""

    # 删掉 openid 行，模拟「找不到 openid」
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM wechat_openid WHERE wechat_user_id = :u"), {"u": seed.wechat_user_id}
        )

    await set_auth(
        owner_engine,
        tenant_id=seed.tenant_id,
        wechat_user_id=seed.wechat_user_id,
        template_id=REMIND,
        status="authorized",
        count=1,
    )
    await enqueue(
        db_factory,
        tenant_id=seed.tenant_id,
        dedupe_key="no-openid",
        payload=wsub_payload(seed.parent_id, scene="lesson_remind"),
    )
    await nc.process_pending_notifications(db_factory)

    assert (await get_notification(db_factory, "no-openid")).status == nc.STATUS_SUCCEEDED
    assert len(stub.calls) == 0
    assert await count_inapp(owner_engine, parent_id=seed.parent_id) == 1
    # 没发出，额度不该被扣
    assert (
        await get_count(
            owner_engine,
            tenant_id=seed.tenant_id,
            wechat_user_id=seed.wechat_user_id,
            template_id=REMIND,
        )
        == 1
    )


async def test_unknown_scene_degrades(db_factory, owner_engine, seed, stub) -> None:
    """模板 scene 不在配置里 → 降级，stub 不收。"""

    await enqueue(
        db_factory,
        tenant_id=seed.tenant_id,
        dedupe_key="bad-scene",
        payload=wsub_payload(seed.parent_id, scene="no_such_scene"),
    )
    await nc.process_pending_notifications(db_factory)

    assert (await get_notification(db_factory, "bad-scene")).status == nc.STATUS_SUCCEEDED
    assert len(stub.calls) == 0
    assert await count_inapp(owner_engine, parent_id=seed.parent_id) == 1
