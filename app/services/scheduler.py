"""调度器（6.4）：自写 asyncio 定时循环，接管 6.1 worker 并驱动两条业务事件。

不引 APScheduler / Celery / arq（发令部硬性要求）。三个定时任务在同一个进程内跑：

* **worker 循环**：30s 一轮（沿用 6.1 的间隔），调 ``process_pending_notifications``；
* **上课提醒扫描**：5 分钟一轮，``scan_lesson_reminders``；
* **余额预警扫描**：每小时一轮，``scan_balance_alerts``。

一期单实例部署，循环不设分布式锁；多实例要上锁或外置调度，二期再办（见 08 §0 交接块）。
``scheduler_enabled=True`` 时旧 ``notification_worker_enabled`` 循环不再启动（main.py 里互斥）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.services.notification_center import (
    SessionFactory,
    process_pending_notifications,
)
from app.services.notification_events import scan_balance_alerts, scan_lesson_reminders

logger = logging.getLogger(__name__)


async def _run_interval(
    *,
    name: str,
    interval_seconds: int,
    stop_event: asyncio.Event,
    job: Callable[[], Awaitable[None]],
) -> None:
    """反复执行 ``job``，两次之间 idle ``interval_seconds``；``stop_event`` 置位即退出。

    任何一轮抛异常都只记日志继续，不退出（通知/提醒坏了不该拖垮整个应用）。
    """

    while not stop_event.is_set():
        try:
            await job()
        except Exception:  # noqa: BLE001
            logger.exception("scheduler_job_failed", extra={"extra_fields": {"job": name}})
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass


async def run_scheduler(
    db_factory: SessionFactory,
    *,
    worker_interval_seconds: int,
    reminder_interval_seconds: int,
    balance_interval_seconds: int,
    stop_event: asyncio.Event,
) -> None:
    """三个定时任务并发跑，``stop_event`` 置位后一起退出。"""

    await asyncio.gather(
        _run_interval(
            name="notification_worker",
            interval_seconds=worker_interval_seconds,
            stop_event=stop_event,
            job=lambda: process_pending_notifications(db_factory),
        ),
        _run_interval(
            name="lesson_reminder_scan",
            interval_seconds=reminder_interval_seconds,
            stop_event=stop_event,
            job=lambda: scan_lesson_reminders(db_factory),
        ),
        _run_interval(
            name="balance_alert_scan",
            interval_seconds=balance_interval_seconds,
            stop_event=stop_event,
            job=lambda: scan_balance_alerts(db_factory),
        ),
    )
