"""通知中心（6.1）—— 引擎 + 状态机 + 幂等 + 重试。

重试语义是本步的灵魂，全部在真实 PostgreSQL 上跑。What must never break：

* 同一 ``dedupe_key`` 重复入队只留一行；
* 全部通道成功 → ``succeeded``；失败按 30s/2min/8min 指数退避，达上限 → ``exhausted``（终态）；
* 重试只发剩余通道，已成功的不重发（家长不重复收）；
* ``FOR UPDATE SKIP LOCKED`` 下两个 worker 并发不重复处理同一行；
* ``in_app`` 通道单家长插入失败只回滚该家长、其余照发，通知本身不算全败；
* worker 循环在 env 开关关闭时不启动。
"""

import asyncio
import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.main import lifespan
from app.models.notification import InAppMessage
from app.models.notification_center import Notification
from app.services import notification_center as nc

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run notification centre tests",
    ),
]


@pytest.fixture
def db_factory(owner_engine):
    return async_sessionmaker(owner_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def isolated_channels():
    """每个测试后把通道注册表恢复到只含内置 in_app，隔离假通道。"""

    before = dict(nc._channels)
    yield
    nc._channels.clear()
    nc._channels.update(before)


@pytest.fixture(autouse=True)
async def clean_slate(owner_engine):
    """每个测试前清空 notification / in_app_message，避免 process 扫到别的测试的 pending 行。"""

    async with owner_engine.begin() as connection:
        await connection.execute(text("DELETE FROM in_app_message"))
        await connection.execute(text("DELETE FROM notification"))
    yield


@pytest.fixture
async def seed(owner_engine):
    """一个机构 + 两个真实家长 + 一个学生（in_app_message 有 parent FK）。"""

    tenant_id, student_id = uuid4(), uuid4()
    parent_a, parent_b = uuid4(), uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, '通知机构')"), {"id": tenant_id}
        )
        await connection.execute(
            text("INSERT INTO student (id, tenant_id, name) VALUES (:id, :t, '小明')"),
            {"id": student_id, "t": tenant_id},
        )
        for parent_id, name in ((parent_a, "家长A"), (parent_b, "家长B")):
            await connection.execute(
                text(
                    "INSERT INTO parent (id, tenant_id, name, phone) "
                    "VALUES (:id, :t, :name, :phone)"
                ),
                {
                    "id": parent_id,
                    "t": tenant_id,
                    "name": name,
                    "phone": f"138{uuid4().int % 10**8:08d}",
                },
            )
    return tenant_id, parent_a, parent_b, student_id


def in_app_payload(parent_ids: list, *, source_id: str | None = None) -> dict:
    return {
        "parent_ids": [str(p) for p in parent_ids],
        "title": "测试标题",
        "body": "测试正文",
        "source_type": "test",
        "source_id": source_id or str(uuid4()),
    }


async def enqueue(db_factory, *, tenant_id, dedupe_key, payload=None, channels=None) -> bool:
    async with db_factory() as db:
        async with db.begin():
            return await nc.enqueue_notification(
                db,
                tenant_id=tenant_id,
                event_type="test_event",
                dedupe_key=dedupe_key,
                payload=payload or {},
                channels=channels or [nc.CHANNEL_IN_APP],
            )


async def get_notification(db_factory, dedupe_key: str) -> Notification:
    async with db_factory() as db:
        return (
            await db.execute(select(Notification).where(Notification.dedupe_key == dedupe_key))
        ).scalar_one()


async def count_messages(db_factory, *, parent_id=None) -> int:
    async with db_factory() as db:
        query = select(func.count()).select_from(InAppMessage)
        if parent_id is not None:
            query = query.where(InAppMessage.parent_id == parent_id)
        return int((await db.scalar(query)) or 0)


async def set_next_retry_to_past(db_factory, dedupe_key: str) -> None:
    async with db_factory() as db:
        async with db.begin():
            await db.execute(
                update(Notification)
                .where(Notification.dedupe_key == dedupe_key)
                .values(next_retry_at=func.now() - text("interval '1 minute'"))
            )


async def test_enqueue_is_idempotent(db_factory, seed) -> None:
    tenant_id, *_ = seed
    for _ in range(10):
        await enqueue(db_factory, tenant_id=tenant_id, dedupe_key="idem-1", payload={})
    async with db_factory() as db:
        total = await db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.dedupe_key == "idem-1")
        )
    assert int(total) == 1


async def test_success_path_writes_in_app_message(db_factory, seed) -> None:
    tenant_id, parent_a, _, _ = seed
    await enqueue(
        db_factory,
        tenant_id=tenant_id,
        dedupe_key="ok-1",
        payload=in_app_payload([parent_a]),
    )
    processed = await nc.process_pending_notifications(db_factory)
    assert processed == 1

    notification = await get_notification(db_factory, "ok-1")
    assert notification.status == nc.STATUS_SUCCEEDED
    assert notification.attempts == 0
    assert await count_messages(db_factory, parent_id=parent_a) == 1


async def test_failure_retries_then_exhausted(db_factory, seed) -> None:
    tenant_id, *_ = seed
    always_fail = _CountingChannel(fail=True)
    nc.register_channel("test_always_fail", always_fail)

    await enqueue(
        db_factory,
        tenant_id=tenant_id,
        dedupe_key="fail-1",
        payload={},
        channels=["test_always_fail"],
    )
    await nc.process_pending_notifications(db_factory)
    notification = await get_notification(db_factory, "fail-1")
    assert notification.status == nc.STATUS_PENDING
    assert notification.attempts == 1
    assert notification.next_retry_at is not None  # 退避到未来

    await set_next_retry_to_past(db_factory, "fail-1")
    await nc.process_pending_notifications(db_factory)
    notification = await get_notification(db_factory, "fail-1")
    assert notification.attempts == 2
    assert notification.status == nc.STATUS_PENDING

    await set_next_retry_to_past(db_factory, "fail-1")
    await nc.process_pending_notifications(db_factory)
    notification = await get_notification(db_factory, "fail-1")
    assert notification.attempts == 3
    assert notification.status == nc.STATUS_EXHAUSTED
    assert always_fail.calls == 3


async def test_retry_succeeds_after_transient_failure(db_factory, seed) -> None:
    tenant_id, parent_a, _, _ = seed
    flaky = _FlakyChannel(fail_times=1)
    nc.register_channel("test_flaky", flaky)

    await enqueue(
        db_factory,
        tenant_id=tenant_id,
        dedupe_key="flaky-1",
        payload=in_app_payload([parent_a]),
        channels=[nc.CHANNEL_IN_APP, "test_flaky"],
    )
    # 第一轮：in_app 成功落库，flaky 失败 → 整条留 pending
    await nc.process_pending_notifications(db_factory)
    notification = await get_notification(db_factory, "flaky-1")
    assert notification.status == nc.STATUS_PENDING
    assert notification.succeeded_channels == [nc.CHANNEL_IN_APP]
    assert await count_messages(db_factory, parent_id=parent_a) == 1

    # 第二轮：flaky 成功，in_app 不再重发 → 最终只落一条站内信
    await set_next_retry_to_past(db_factory, "flaky-1")
    await nc.process_pending_notifications(db_factory)
    notification = await get_notification(db_factory, "flaky-1")
    assert notification.status == nc.STATUS_SUCCEEDED
    assert await count_messages(db_factory, parent_id=parent_a) == 1
    assert flaky.calls == 2


async def test_partial_channel_success_skips_successful_on_retry(db_factory, seed) -> None:
    tenant_id, *_ = seed
    ok = _CountingChannel(fail=False)
    fail = _CountingChannel(fail=True)
    nc.register_channel("test_part_ok", ok)
    nc.register_channel("test_part_fail", fail)

    await enqueue(
        db_factory,
        tenant_id=tenant_id,
        dedupe_key="part-1",
        payload={},
        channels=["test_part_ok", "test_part_fail"],
    )
    await nc.process_pending_notifications(db_factory)
    notification = await get_notification(db_factory, "part-1")
    assert notification.status == nc.STATUS_PENDING
    assert notification.succeeded_channels == ["test_part_ok"]
    assert ok.calls == 1
    assert fail.calls == 1

    # 重试只发失败的 test_part_fail，成功过的 test_part_ok 不再发
    await set_next_retry_to_past(db_factory, "part-1")
    await nc.process_pending_notifications(db_factory)
    notification = await get_notification(db_factory, "part-1")
    assert fail.calls == 2
    assert ok.calls == 1
    assert notification.succeeded_channels == ["test_part_ok"]


async def test_concurrent_process_no_double_dispatch(db_factory, seed) -> None:
    tenant_id, parent_a, _, _ = seed
    await enqueue(
        db_factory,
        tenant_id=tenant_id,
        dedupe_key="conc-1",
        payload=in_app_payload([parent_a]),
    )
    results = await asyncio.gather(
        nc.process_pending_notifications(db_factory),
        nc.process_pending_notifications(db_factory),
    )
    assert sum(results) == 1
    notification = await get_notification(db_factory, "conc-1")
    assert notification.status == nc.STATUS_SUCCEEDED
    assert await count_messages(db_factory, parent_id=parent_a) == 1


async def test_in_app_per_parent_failure_is_tolerated(db_factory, seed) -> None:
    tenant_id, parent_a, *_ = seed
    missing_parent = uuid4()  # 不存在的 parent_id → FK 违规，只回滚这一位家长
    await enqueue(
        db_factory,
        tenant_id=tenant_id,
        dedupe_key="partial-parent-1",
        payload=in_app_payload([parent_a, missing_parent]),
    )
    await nc.process_pending_notifications(db_factory)

    notification = await get_notification(db_factory, "partial-parent-1")
    assert notification.status == nc.STATUS_SUCCEEDED  # 单家长失败不算全败
    assert await count_messages(db_factory, parent_id=parent_a) == 1


async def test_worker_loop_processes_pending_and_stops(db_factory, seed) -> None:
    tenant_id, parent_a, _, _ = seed
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        nc.run_notification_worker(db_factory, interval_seconds=1, stop_event=stop_event),
        name="test-notification-worker",
    )
    try:
        await enqueue(
            db_factory,
            tenant_id=tenant_id,
            dedupe_key="worker-1",
            payload=in_app_payload([parent_a]),
        )
        for _ in range(50):
            notification = await get_notification(db_factory, "worker-1")
            if notification.status == nc.STATUS_SUCCEEDED:
                break
            await asyncio.sleep(0.1)
        assert notification.status == nc.STATUS_SUCCEEDED
    finally:
        stop_event.set()
        await asyncio.wait_for(task, timeout=5)


async def test_lifespan_skips_worker_when_disabled(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "notification_worker_enabled", False)

    async with lifespan(FastAPI()):
        names = {task.get_name() for task in asyncio.all_tasks()}
        assert "notification-worker" not in names


class _CountingChannel:
    """可计数、可选必失败的假通道。"""

    def __init__(self, *, fail: bool) -> None:
        self.fail = fail
        self.calls = 0

    async def send(self, ctx: nc.NotificationContext) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("counting channel boom")


class _FlakyChannel:
    """前 ``fail_times`` 次失败，之后成功。"""

    def __init__(self, *, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    async def send(self, ctx: nc.NotificationContext) -> None:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("flaky boom")
