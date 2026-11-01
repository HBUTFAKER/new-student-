"""通知事件（6.4）—— 上课提醒 + 余额预警的扫描与按家长拆分 enqueue。

全部在真实 PostgreSQL 上跑（owner 连接跨租户扫描）。What must never break：

* 上课提醒：提前 1 天 / 1 小时各入队一次（两个时点幂等键不同）；cancelled/moved 课不发；
  夜间静默（22:00–08:00）不入队、到点后下一轮扫描自然捡起；
* 余额预警：余额 < 阈值入队；每新增一条 consume 流水可再入队；同一流水对应扫描轮次不重复；
  阈值 0 关闭；
* 按家长拆分：N 个家长 = N 条 notification，各带独立 dedupe_key。
"""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services import notification_events as ne
from app.services.ledger import BUSINESS_TZ

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run notification event tests",
    ),
]


@dataclass(frozen=True, slots=True)
class Seed:
    tenant_id: UUID
    teacher_id: UUID
    classroom_id: UUID
    class_id: UUID
    student_id: UUID
    parent_ids: tuple[UUID, ...]


@pytest.fixture
def db_factory(owner_engine):
    return async_sessionmaker(owner_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def clean_slate(owner_engine):
    """清空出站队列与站内信，避免扫到别的测试留下的 pending 行。"""

    async with owner_engine.begin() as connection:
        await connection.execute(text("DELETE FROM in_app_message"))
        await connection.execute(text("DELETE FROM notification"))
    yield


@pytest.fixture
async def seed(owner_engine, request) -> Seed:
    """一个机构 + 一班 + 一学生 + N 个家长（含绑定 + 报名），不建课次（由测试各自建）。"""

    parent_count = getattr(request, "param", 2)
    tenant_id, teacher_id = uuid4(), uuid4()
    plan_id = uuid4()
    classroom_id, course_id, class_id = uuid4(), uuid4(), uuid4()
    student_id = uuid4()
    parent_ids = tuple(uuid4() for _ in range(parent_count))

    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, '提醒事件机构')"), {"id": tenant_id}
        )
        # 7.2 起扫描只挑未到期租户（join subscription），seed 补一条远未来的订阅。
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
                "VALUES (:id, :t, :p, '2026-01-01', '2099-12-31', 'active')"
            ),
            {"id": uuid4(), "t": tenant_id, "p": plan_id},
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
        for i, parent_id in enumerate(parent_ids):
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
            await connection.execute(
                text(
                    "INSERT INTO parent_student (id, tenant_id, parent_id, student_id, relation) "
                    "VALUES (:id, :t, :p, :s, '家长')"
                ),
                {"id": uuid4(), "t": tenant_id, "p": parent_id, "s": student_id},
            )
        # 默认配置：提醒 1440,60 + 余额阈值 5。
        await connection.execute(
            text(
                "INSERT INTO org_config (tenant_id, absence_deduct_rule, lesson_reminder_hours, "
                "balance_alert_threshold) VALUES (:t, 'none', '1440,60', 5)"
            ),
            {"t": tenant_id},
        )
    return Seed(
        tenant_id=tenant_id,
        teacher_id=teacher_id,
        classroom_id=classroom_id,
        class_id=class_id,
        student_id=student_id,
        parent_ids=parent_ids,
    )


async def insert_lesson(engine, seed: Seed, *, start_at: datetime, status: str = "normal") -> UUID:
    lesson_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO lesson (id, tenant_id, class_id, classroom_id, teacher_id, "
                "start_at, end_at, status) VALUES (:id, :t, :c, :r, :th, :start, :end, :status)"
            ),
            {
                "id": lesson_id,
                "t": seed.tenant_id,
                "c": seed.class_id,
                "r": seed.classroom_id,
                "th": seed.teacher_id,
                "start": start_at,
                "end": start_at + timedelta(hours=1),
                "status": status,
            },
        )
    return lesson_id


async def count_notifications(engine, *, tenant_id: UUID, event_type: str) -> int:
    async with engine.connect() as connection:
        return int(
            await connection.scalar(
                text("SELECT count(*) FROM notification WHERE tenant_id = :t AND event_type = :e"),
                {"t": tenant_id, "e": event_type},
            )
            or 0
        )


async def insert_ledger(
    engine, seed: Seed, *, delta: int, type_: str, reason: str = "测试流水"
) -> UUID:
    entry_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO lesson_ledger (id, tenant_id, student_id, type, delta, reason, "
                "operator_id) VALUES (:id, :t, :s, :type, :delta, :reason, :op)"
            ),
            {
                "id": entry_id,
                "t": seed.tenant_id,
                "s": seed.student_id,
                "type": type_,
                "delta": delta,
                "reason": reason,
                "op": uuid4(),
            },
        )
    return entry_id


async def test_lesson_reminder_two_offsets_each_fire_once(db_factory, owner_engine, seed) -> None:
    """提前 1 天与提前 1 小时是两个独立提醒，各入队一次；重复扫描不重复。"""

    start = datetime(2026, 12, 1, 18, 0, tzinfo=BUSINESS_TZ)
    await insert_lesson(owner_engine, seed, start_at=start)

    # 提前 1 天（offset 1440）
    await ne.scan_lesson_reminders(db_factory, now=start - timedelta(minutes=1440))
    assert await count_notifications(
        owner_engine, tenant_id=seed.tenant_id, event_type=ne.EVENT_LESSON_REMINDER
    ) == len(seed.parent_ids)

    # 同一时点再扫 → dedupe 不重复
    await ne.scan_lesson_reminders(db_factory, now=start - timedelta(minutes=1440))
    assert await count_notifications(
        owner_engine, tenant_id=seed.tenant_id, event_type=ne.EVENT_LESSON_REMINDER
    ) == len(seed.parent_ids)

    # 提前 1 小时（offset 60）→ 新增一组（幂等键含 offset，不跟 1 天的撞）
    await ne.scan_lesson_reminders(db_factory, now=start - timedelta(minutes=60))
    assert await count_notifications(
        owner_engine, tenant_id=seed.tenant_id, event_type=ne.EVENT_LESSON_REMINDER
    ) == 2 * len(seed.parent_ids)


async def test_cancelled_lesson_not_reminded(db_factory, owner_engine, seed) -> None:
    """cancelled / moved 课次不发提醒。"""

    start = datetime(2026, 12, 1, 18, 0, tzinfo=BUSINESS_TZ)
    await insert_lesson(owner_engine, seed, start_at=start, status="cancelled")
    await ne.scan_lesson_reminders(db_factory, now=start - timedelta(minutes=60))
    assert (
        await count_notifications(
            owner_engine, tenant_id=seed.tenant_id, event_type=ne.EVENT_LESSON_REMINDER
        )
        == 0
    )


async def test_silent_window_defers_one_day_reminder(db_factory, owner_engine, seed) -> None:
    """0:30 的课，提前 1 天提醒：0:30 在静默窗口不入队，8:00 后下一轮扫描自然捡起。"""

    start = datetime(2026, 12, 3, 0, 30, tzinfo=BUSINESS_TZ)
    await insert_lesson(owner_engine, seed, start_at=start)

    await ne.scan_lesson_reminders(db_factory, now=datetime(2026, 12, 2, 0, 30, tzinfo=BUSINESS_TZ))
    assert (
        await count_notifications(
            owner_engine, tenant_id=seed.tenant_id, event_type=ne.EVENT_LESSON_REMINDER
        )
        == 0
    )

    await ne.scan_lesson_reminders(db_factory, now=datetime(2026, 12, 2, 8, 0, tzinfo=BUSINESS_TZ))
    assert await count_notifications(
        owner_engine, tenant_id=seed.tenant_id, event_type=ne.EVENT_LESSON_REMINDER
    ) == len(seed.parent_ids)


async def test_silent_window_one_hour_reminder_before_22(db_factory, owner_engine, seed) -> None:
    """22:30 的课，提前 1 小时提醒在 21:30（未到 22:00）入队。"""

    start = datetime(2026, 12, 1, 22, 30, tzinfo=BUSINESS_TZ)
    await insert_lesson(owner_engine, seed, start_at=start)

    await ne.scan_lesson_reminders(
        db_factory, now=datetime(2026, 12, 1, 21, 30, tzinfo=BUSINESS_TZ)
    )
    assert await count_notifications(
        owner_engine, tenant_id=seed.tenant_id, event_type=ne.EVENT_LESSON_REMINDER
    ) == len(seed.parent_ids)


async def test_balance_alert_fires_per_consume(db_factory, owner_engine, seed) -> None:
    """余额 < 阈值入队；无 consume 用 init；新增 consume 后可再入队；同流水不重复。"""

    # 余额 4（< 5），无 consume → dedupe 用 init
    await insert_ledger(owner_engine, seed, delta=4, type_="purchase")
    await ne.scan_balance_alerts(db_factory, now=datetime(2026, 12, 1, 12, 0, tzinfo=BUSINESS_TZ))
    assert await count_notifications(
        owner_engine, tenant_id=seed.tenant_id, event_type=ne.EVENT_BALANCE_ALERT
    ) == len(seed.parent_ids)

    # 同一条流水（无 consume）再扫 → 不重复
    await ne.scan_balance_alerts(db_factory, now=datetime(2026, 12, 1, 13, 0, tzinfo=BUSINESS_TZ))
    assert await count_notifications(
        owner_engine, tenant_id=seed.tenant_id, event_type=ne.EVENT_BALANCE_ALERT
    ) == len(seed.parent_ids)

    # 再扣 1 课时 → 新 consume 流水 → 余额 3，可再提醒
    await insert_ledger(owner_engine, seed, delta=-1, type_="consume")
    await ne.scan_balance_alerts(db_factory, now=datetime(2026, 12, 1, 14, 0, tzinfo=BUSINESS_TZ))
    assert await count_notifications(
        owner_engine, tenant_id=seed.tenant_id, event_type=ne.EVENT_BALANCE_ALERT
    ) == 2 * len(seed.parent_ids)


async def test_balance_alert_threshold_zero_disables(db_factory, owner_engine, seed) -> None:
    """阈值 0 = 关闭预警。"""

    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE org_config SET balance_alert_threshold = 0 WHERE tenant_id = :t"),
            {"t": seed.tenant_id},
        )
    await insert_ledger(owner_engine, seed, delta=1, type_="purchase")
    await ne.scan_balance_alerts(db_factory, now=datetime(2026, 12, 1, 12, 0, tzinfo=BUSINESS_TZ))
    assert (
        await count_notifications(
            owner_engine, tenant_id=seed.tenant_id, event_type=ne.EVENT_BALANCE_ALERT
        )
        == 0
    )


async def test_balance_alert_above_threshold_not_fired(db_factory, owner_engine, seed) -> None:
    """余额 >= 阈值不入队。"""

    await insert_ledger(owner_engine, seed, delta=8, type_="purchase")
    await ne.scan_balance_alerts(db_factory, now=datetime(2026, 12, 1, 12, 0, tzinfo=BUSINESS_TZ))
    assert (
        await count_notifications(
            owner_engine, tenant_id=seed.tenant_id, event_type=ne.EVENT_BALANCE_ALERT
        )
        == 0
    )
