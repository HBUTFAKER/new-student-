"""通知中心（6.1）：幂等入队 + 通道适配器 + 带重试的 worker。

本模块只交付**引擎**，不接具体业务事件。6.2 接微信订阅通道、6.4 接定时任务时再接入
（见 ``docs/phase0/08-implementation-delta.md`` §0 的 6.1 交接块）。

设计要点：

* ``dedupe_key`` 是事件幂等的根：入队 ``INSERT ... ON CONFLICT (dedupe_key) DO NOTHING``，
  「同一事件不重复发」的第一道防线（禁止先查后插）。
* 状态机三态：``pending → succeeded``（全部通道成功）或 ``pending → exhausted``（重试
  次数用尽，终态不重扫）。失败不单独存一态，失败后仍留在 ``pending``、``attempts + 1``、
  ``next_retry_at`` 指数退避（30s × 4ⁿ）。
* ``succeeded_channels`` 记录已成功通道，重试只发剩余通道——部分成功不重复发。
* ``process_pending_notifications`` 用 ``FOR UPDATE SKIP LOCKED``，两个 worker 并发不会拿
  同一行。单实例部署安全（一期如此）；多实例要上分布式锁（6.4 再评估）。
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import InAppMessage
from app.models.notification_center import Notification
from app.services.ledger import BUSINESS_TZ

logger = logging.getLogger(__name__)

#: 状态机三态（没有独立的 failed 态：失败仍 pending，靠 attempts 计数推进到 exhausted）。
STATUS_PENDING = "pending"
STATUS_SUCCEEDED = "succeeded"
STATUS_EXHAUSTED = "exhausted"

#: 指数退避：30s / 2min / 8min（基准 30s × 4ⁿ）。
_RETRY_BASE_SECONDS = 30
_RETRY_MULTIPLIER = 4
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BATCH_SIZE = 50

#: 本步真实可用的通道。
CHANNEL_IN_APP = "in_app"

#: ``async with db_factory() as db`` 拿到一个 AsyncSession（通常是 owner 连接，绕过 RLS
#: 跨租户扫描）。进程内 worker 和测试各自传入自己的 sessionmaker。
SessionFactory = Callable[[], AsyncSession]


@dataclass(slots=True)
class NotificationContext:
    """一次通道发送的上下文。adapter 只依赖它，不碰 Notification 行本身。"""

    db: AsyncSession
    tenant_id: UUID
    event_type: str
    dedupe_key: str
    payload: dict


class ChannelAdapter(Protocol):
    """通道适配器接口：``send`` 抛异常 = 该通道本轮失败。"""

    async def send(self, ctx: NotificationContext) -> None: ...


_channels: dict[str, ChannelAdapter] = {}


def register_channel(name: str, adapter: ChannelAdapter) -> None:
    """注册（或覆盖）一个通道。6.2 接微信订阅通道时用同一个入口。"""

    _channels[name] = adapter


def unregister_channel(name: str) -> None:
    """测试与热替换用；生产代码不调。"""

    _channels.pop(name, None)


def get_channel(name: str) -> ChannelAdapter | None:
    return _channels.get(name)


async def enqueue_notification(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    event_type: str,
    dedupe_key: str,
    payload: dict,
    channels: list[str],
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> bool:
    """幂等入队一条通知。同一 ``dedupe_key`` 重复入队静默忽略，返回是否新建。

    调用方负责提交事务（与 ``record_lesson_deduct`` 同口径）。``next_retry_at`` 走
    server_default ``now()``，与 worker 扫描用的 ``func.now()`` 同源。
    """

    stmt = (
        pg_insert(Notification)
        .values(
            tenant_id=tenant_id,
            event_type=event_type,
            dedupe_key=dedupe_key,
            payload=payload,
            channels=channels,
            max_attempts=max_attempts,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )
    result = await db.execute(stmt)
    return bool(result.rowcount)


class InAppChannel:
    """真实通道 ``in_app``：把 ``payload`` 解析成 ``in_app_message`` 落库。

    ``payload`` 约定（6.2/6.4 接入事件时按此填）：

    * ``parent_ids``: ``[uuid, ...]`` —— 必填，收信家长；
    * ``type``: 站内信类型（缺省用 ``event_type``）；
    * ``title`` / ``body``: 文案；
    * ``source_type`` / ``source_id``: 幂等键，与 ``in_app_message`` 同构；
    * ``student_id``: 可选，消息涉及的孩子。

    按家长循环逐个 ``SAVEPOINT``：单家长插入失败只回滚该家长、其余照发，通道整体
    **不**因此判失败（站内信是兜底，个别家长收不到不该拖垮整条通知反复重试）。
    """

    async def send(self, ctx: NotificationContext) -> None:
        parent_ids = ctx.payload.get("parent_ids")
        if not parent_ids:
            raise ValueError("in_app payload 缺 parent_ids")
        source_id = ctx.payload.get("source_id")
        if not source_id:
            raise ValueError("in_app payload 缺 source_id")

        student_id = ctx.payload.get("student_id")
        title = str(ctx.payload.get("title", ""))
        body = str(ctx.payload.get("body", ""))
        message_type = str(ctx.payload.get("type", ctx.event_type))
        source_type = str(ctx.payload.get("source_type", ctx.event_type))

        for parent_id in parent_ids:
            try:
                async with ctx.db.begin_nested():
                    await ctx.db.execute(
                        pg_insert(InAppMessage)
                        .values(
                            tenant_id=ctx.tenant_id,
                            parent_id=UUID(str(parent_id)),
                            student_id=UUID(str(student_id)) if student_id else None,
                            type=message_type,
                            title=title,
                            body=body,
                            source_type=source_type,
                            source_id=UUID(str(source_id)),
                        )
                        .on_conflict_do_nothing(
                            index_elements=["source_type", "source_id", "parent_id"]
                        )
                    )
            except Exception:
                logger.exception(
                    "in_app_channel_per_parent_failed",
                    extra={
                        "extra_fields": {
                            "dedupe_key": ctx.dedupe_key,
                            "parent_id": str(parent_id),
                        }
                    },
                )


register_channel(CHANNEL_IN_APP, InAppChannel())


def _retry_delay_seconds(attempts: int) -> int:
    """失败后的第 ``attempts`` 次尝试 → 30s × 4^(attempts-1) = 30s / 2min / 8min。"""

    return _RETRY_BASE_SECONDS * (_RETRY_MULTIPLIER ** (attempts - 1))


async def _dispatch(db: AsyncSession, notification: Notification) -> None:
    """在已开启的事务里，把一条通知按状态机推进一档。"""

    succeeded = list(notification.succeeded_channels or [])
    remaining = [channel for channel in notification.channels if channel not in succeeded]
    last_error: str | None = None

    for channel in remaining:
        adapter = get_channel(channel)
        try:
            if adapter is None:
                raise ValueError(f"未注册的通道: {channel}")
            await adapter.send(
                NotificationContext(
                    db=db,
                    tenant_id=notification.tenant_id,
                    event_type=notification.event_type,
                    dedupe_key=notification.dedupe_key,
                    payload=notification.payload or {},
                )
            )
            succeeded.append(channel)
        except Exception as exc:  # noqa: BLE001 — 任何通道失败都转成状态机推进，不冒泡
            last_error = str(exc)
            break  # 本轮不再发剩余通道，等下次重试

    now = datetime.now(BUSINESS_TZ)
    notification.succeeded_channels = succeeded
    notification.updated_at = now
    if last_error is None:
        notification.status = STATUS_SUCCEEDED
        return

    notification.attempts += 1
    notification.last_error = last_error[:2000]
    if notification.attempts >= notification.max_attempts:
        notification.status = STATUS_EXHAUSTED
    else:
        notification.status = STATUS_PENDING
        notification.next_retry_at = now + timedelta(
            seconds=_retry_delay_seconds(notification.attempts)
        )


async def process_pending_notifications(
    db_factory: SessionFactory, *, batch_size: int = DEFAULT_BATCH_SIZE
) -> int:
    """处理一批到点的 pending 通知，返回处理条数。

    每条通知一个独立事务 + ``FOR UPDATE SKIP LOCKED``：一条失败不影响其他，且两个
    worker 并发时各拿各的行。外层循环最多 ``batch_size`` 次，扫不到待发就提前退出。
    """

    processed = 0
    for _ in range(batch_size):
        async with db_factory() as db:
            async with db.begin():
                row = (
                    await db.execute(
                        select(Notification)
                        .where(
                            Notification.status == STATUS_PENDING,
                            Notification.next_retry_at <= func.now(),
                        )
                        .order_by(Notification.next_retry_at, Notification.id)
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                ).scalar_one_or_none()
                if row is None:
                    break
                await _dispatch(db, row)
                processed += 1
    return processed


async def run_notification_worker(
    db_factory: SessionFactory,
    *,
    interval_seconds: int,
    stop_event: asyncio.Event,
) -> None:
    """进程内轮询循环：反复扫 pending 队列，直到 ``stop_event`` 置位。

    ``interval_seconds`` 是两次扫描之间的空闲时长；单次 ``process`` 内部已经逐条处理，
    这里不额外 sleep 到处理完。任何一轮抛异常都只记日志继续，不退出（通知中心坏了
    不该拖垮整个应用）。
    """

    while not stop_event.is_set():
        try:
            await process_pending_notifications(db_factory)
        except Exception:  # noqa: BLE001
            logger.exception("notification_worker_cycle_failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass
