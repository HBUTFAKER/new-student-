"""1000 条模拟通知验收（6.4）：真库、真 worker、真降级链，输出成功率 / exhausted / 耗时。

- 造 1 租户 + 500 家长，每人 2 条通知 = 1000 条；
- 950 条走完整降级链（订阅 → 服务号 → 短信 → 站内信），家长未绑微信 / 机构未配服务号与短信
  → 依次跳过、最终站内信兜底送达（succeeded）；
- 50 条故意走「无站内信兜底」的链（订阅 → 服务号 → 短信），全部永久跳过 → exhausted（漏发）；
- 收敛后输出成功率、exhausted 明细、总耗时；再演示 retry 补发：把 exhausted 重置回 pending
  （即 ``POST /admin/notifications/{id}/retry`` 的落库效果）并补上站内信通道后重新处理 → 全部送达。

CI 不跑（本地 / 验收环境跑）。可重复跑：先按机构名清掉自己的数据。

用法：``python -m scripts.simulate_notifications``（需 ``RUN_DB_TESTS=1`` 且 PostgreSQL 可达）。
"""

from __future__ import annotations

import asyncio
import time
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.services import notification_center as nc
from app.services.channels import sms, wechat_mp, wechat_subscribe  # noqa: F401 导入即注册通道

TENANT_NAME = "模拟验收机构"
PARENT_COUNT = 500
FAIL_PARENT_COUNT = 25  # 25 个家长 × 2 条 = 50 条必失败

#: 完整降级链（站内信兜底）→ 一定送达。
CHAIN_OK = ["wechat_subscribe", "wechat_mp", "sms", "in_app"]
#: 无站内信兜底 → 该家长无可用通道时 exhausted（漏发）。
CHAIN_FAIL = ["wechat_subscribe", "wechat_mp", "sms"]


def _payload(parent_id) -> dict:
    return {
        "parent_ids": [str(parent_id)],
        "template_scene": "lesson_remind",
        "title": "上课提醒",
        "body": "模拟通知",
        "source_type": "sim",
        "source_id": str(uuid4()),
    }


async def _counts(db_factory, tenant_id) -> dict[str, int]:
    async with db_factory() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT status, count(*) FROM notification WHERE tenant_id = :t GROUP BY status"
                ),
                {"t": tenant_id},
            )
        ).all()
    return {status: int(n) for status, n in rows}


async def _drain(db_factory) -> int:
    """跑 worker 至收敛（无到点 pending 即停），返回处理总条数。"""

    total = 0
    for _ in range(200):  # 防死循环护栏；正常一两轮就收敛
        processed = await nc.process_pending_notifications(db_factory, batch_size=500)
        total += processed
        if processed == 0:
            break
    return total


async def _reset_exhausted(db_factory, tenant_id) -> int:
    """把 exhausted 重置回 pending（= retry 接口的落库效果）。"""

    async with db_factory() as db:
        async with db.begin():
            result = await db.execute(
                text(
                    "UPDATE notification SET status='pending', attempts=0, next_retry_at=now(), "
                    "last_error=NULL, succeeded_channels=NULL "
                    "WHERE tenant_id = :t AND status = 'exhausted'"
                ),
                {"t": tenant_id},
            )
            return int(result.rowcount or 0)


async def _fix_failed_channels(db_factory, tenant_id) -> int:
    """补发演示：给漏发通知补上站内信通道（模拟「修复了送达路径」）。"""

    async with db_factory() as db:
        async with db.begin():
            result = await db.execute(
                text(
                    "UPDATE notification SET channels = :channels "
                    "WHERE tenant_id = :t AND status = 'pending'"
                ),
                {"channels": CHAIN_OK, "t": tenant_id},
            )
            return int(result.rowcount or 0)


async def run() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.migration_database_url)
    db_factory = async_sessionmaker(engine, expire_on_commit=False)
    started = time.perf_counter()

    try:
        # 1. 清自己的数据（可重复跑）。
        async with engine.begin() as connection:
            existing = await connection.scalar(
                text("SELECT id FROM tenant WHERE name = :n"), {"n": TENANT_NAME}
            )
            if existing is not None:
                await connection.execute(
                    text("DELETE FROM in_app_message WHERE tenant_id = :t"), {"t": existing}
                )
                await connection.execute(
                    text("DELETE FROM notification WHERE tenant_id = :t"), {"t": existing}
                )
                await connection.execute(
                    text("DELETE FROM parent WHERE tenant_id = :t"), {"t": existing}
                )
                await connection.execute(text("DELETE FROM tenant WHERE id = :t"), {"t": existing})

        # 2. 造 1 租户 + 500 家长。
        tenant_id = uuid4()
        parent_ids: list = []
        async with engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
                {"id": tenant_id, "name": TENANT_NAME},
            )
            for i in range(PARENT_COUNT):
                parent_id = uuid4()
                parent_ids.append(parent_id)
                await connection.execute(
                    text(
                        "INSERT INTO parent (id, tenant_id, name, phone) "
                        "VALUES (:id, :t, :name, :phone)"
                    ),
                    {
                        "id": parent_id,
                        "t": tenant_id,
                        "name": f"家长{i}",
                        "phone": f"138{uuid4().int % 10**8:08d}",
                    },
                )

        # 3. 入队 1000 条：前 475 家长 2 条都走兜底链（成功）；后 25 家长 2 条走无兜底链（必失败）。
        async with db_factory() as db:
            async with db.begin():
                for i, parent_id in enumerate(parent_ids):
                    fail = i >= PARENT_COUNT - FAIL_PARENT_COUNT
                    channels = CHAIN_FAIL if fail else CHAIN_OK
                    for _ in range(2):
                        await nc.enqueue_notification(
                            db,
                            tenant_id=tenant_id,
                            event_type="sim",
                            dedupe_key=f"sim:{uuid4()}",
                            payload=_payload(parent_id),
                            channels=channels,
                        )

        # 4. 跑 worker 至收敛。
        processed = await _drain(db_factory)
        elapsed = time.perf_counter() - started
        counts = await _counts(db_factory, tenant_id)
        total = sum(counts.values())
        succeeded = counts.get("succeeded", 0)
        exhausted = counts.get("exhausted", 0)
        pending = counts.get("pending", 0)
        success_rate = succeeded / total if total else 0.0

        print("=" * 60)
        print("1000 条模拟通知验收（6.4）")
        print("=" * 60)
        print(f"租户：{TENANT_NAME}（{tenant_id}）")
        print(f"家长：{PARENT_COUNT} × 2 条 = {total} 条")
        print(f"worker 处理：{processed} 次派发")
        print(f"结果：succeeded={succeeded}  exhausted={exhausted}  pending={pending}")
        print(f"成功率：{success_rate:.4f}（{succeeded}/{total}）")
        print(f"总耗时：{elapsed:.2f}s")
        print("-" * 60)
        assert exhausted == FAIL_PARENT_COUNT * 2, (
            f"预期 {FAIL_PARENT_COUNT * 2} 条 exhausted，实际 {exhausted}"
        )
        print(f"漏发（exhausted）：{exhausted} 条 ✅ 与预期一致")

        # 5. 漏发可补演示：reset → pending，补上站内信通道 → 重新处理 → 全部送达。
        reset = await _reset_exhausted(db_factory, tenant_id)
        await _fix_failed_channels(db_factory, tenant_id)
        await _drain(db_factory)
        after = await _counts(db_factory, tenant_id)
        print("-" * 60)
        print(f"retry 补发：重置 {reset} 条 exhausted → pending 并补上通道后重跑")
        print(
            f"最终：succeeded={after.get('succeeded', 0)}  "
            f"exhausted={after.get('exhausted', 0)}  pending={after.get('pending', 0)}"
        )
        print("=" * 60)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
