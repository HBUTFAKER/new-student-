"""调度器（6.4）—— 三任务并发循环 + lifespan 互斥接线。

覆盖：scheduler_enabled=True 时起调度器（含 worker 循环）且不起 6.1 内置 worker；
scheduler_enabled=False + notification_worker_enabled=True 时回落到 6.1 worker；
run_scheduler 的 worker 循环能处理 pending 通知并优雅停止。
"""

import asyncio
import os
from contextlib import suppress
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.main import lifespan
from app.services import notification_center as nc
from app.services.scheduler import run_scheduler

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run scheduler tests",
    ),
]


@pytest.fixture
def db_factory(owner_engine):
    return async_sessionmaker(owner_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def clean_slate(owner_engine):
    async with owner_engine.begin() as connection:
        await connection.execute(text("DELETE FROM in_app_message"))
        await connection.execute(text("DELETE FROM notification"))
    yield


async def test_lifespan_starts_scheduler_not_worker(monkeypatch) -> None:
    """scheduler_enabled=True 起调度器，旧 worker 循环不再启动（互斥）。"""

    settings = get_settings()
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    monkeypatch.setattr(settings, "notification_worker_enabled", False)

    async with lifespan(FastAPI()):
        names = {task.get_name() for task in asyncio.all_tasks()}
        assert "notification-scheduler" in names
        assert "notification-worker" not in names


async def test_lifespan_falls_back_to_worker(monkeypatch) -> None:
    """scheduler_enabled=False 时回落到 6.1 的内置 worker 循环。"""

    settings = get_settings()
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(settings, "notification_worker_enabled", True)

    async with lifespan(FastAPI()):
        names = {task.get_name() for task in asyncio.all_tasks()}
        assert "notification-worker" in names
        assert "notification-scheduler" not in names


async def test_scheduler_processes_pending_and_stops(db_factory, owner_engine) -> None:
    """run_scheduler 的 worker 循环能处理 pending 通知，stop_event 置位后退出。"""

    tenant_id, parent_id = uuid4(), uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, '调度器机构')"), {"id": tenant_id}
        )
        await connection.execute(
            text("INSERT INTO parent (id, tenant_id, name, phone) VALUES (:id, :t, '家长', :p)"),
            {"id": parent_id, "t": tenant_id, "p": f"138{uuid4().int % 10**8:08d}"},
        )

    stop_event = asyncio.Event()
    task = asyncio.create_task(
        run_scheduler(
            db_factory,
            worker_interval_seconds=1,
            reminder_interval_seconds=3600,
            balance_interval_seconds=3600,
            stop_event=stop_event,
        ),
        name="test-scheduler",
    )
    try:
        async with db_factory() as db:
            async with db.begin():
                await nc.enqueue_notification(
                    db,
                    tenant_id=tenant_id,
                    event_type="test_event",
                    dedupe_key="sched-1",
                    payload={
                        "parent_ids": [str(parent_id)],
                        "title": "测试",
                        "body": "正文",
                        "source_type": "test",
                        "source_id": str(uuid4()),
                    },
                    channels=[nc.CHANNEL_IN_APP],
                )
        for _ in range(50):
            async with db_factory() as db:
                status = await db.scalar(
                    text("SELECT status FROM notification WHERE dedupe_key = 'sched-1'")
                )
            if status == nc.STATUS_SUCCEEDED:
                break
            await asyncio.sleep(0.1)
        assert status == nc.STATUS_SUCCEEDED
    finally:
        stop_event.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
