"""通知事件（6.4）：上课提醒 + 余额预警的扫描与 enqueue。

两条事件都只做「扫描 → 按家长拆分 enqueue」，不碰 6.1 引擎、不碰账本/排课（只读它们的
数据），发送交给 6.1 的通道降级链。定时调度在 ``scheduler.py``。

关键口径（发令部拍板，照做不讨论）：

* 上课提醒两个时点：提前 1 天（1440 分钟）+ 提前 1 小时（60 分钟），机构可配
  ``org_config.lesson_reminder_hours``（逗号分隔分钟数，空串=不发提醒）。
* 余额预警阈值：学生课时余额 < ``org_config.balance_alert_threshold``（默认 5，0=关闭）。
  余额一律以 ledger 的 ``SUM(delta)`` 现算，禁止另算。
* 夜间静默 22:00–08:00（+08:00）：外发通道不在此窗口 enqueue，到点后下一轮扫描自然捡起
  （dedupe 防重）。站内信不受静默限制（034 钩子同事务写入，本服务不碰）。
* 按家长拆分 enqueue：一条业务事件产生 N 个家长 → N 条 notification（dedupe_key 含
  parent_id），从根上消灭「某家长临时失败导致别人重收」。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.archive import ParentStudent, Student
from app.models.course import Class, ClassEnrollment
from app.models.ledger import LessonLedger
from app.models.org import OrgConfig
from app.models.platform import Tenant, TenantSubscription
from app.models.schedule import Lesson
from app.services.ledger import BUSINESS_TZ
from app.services.notification_center import SessionFactory, enqueue_notification

#: 事件类型（notification.event_type 与 in_app_message.source_type 都用它）。
EVENT_LESSON_REMINDER = "lesson_reminder"
EVENT_BALANCE_ALERT = "balance_alert"

#: 完整降级链（对齐 0.7 时序图）：订阅 → 服务号 → 短信 → 站内信。
CHANNEL_CHAIN = ["wechat_subscribe", "wechat_mp", "sms", "in_app"]

#: 上课提醒提前分钟数缺省（org_config 无行时的兜底）。发令部拍板 1440 + 60。
DEFAULT_LESSON_REMINDER_OFFSETS: tuple[int, ...] = (1440, 60)
#: 余额预警阈值缺省（org_config 无行时的兜底）；0 = 关闭预警。
DEFAULT_BALANCE_ALERT_THRESHOLD = 5

#: 夜间静默窗口：22:00–08:00（+08:00），外发通道不在此窗口入队。
_SILENT_START_HOUR = 22
_SILENT_END_HOUR = 8

#: 订阅消息模板 scene（与 config._default_subscribe_templates 对齐）。
SCENE_LESSON_REMIND = "lesson_remind"
SCENE_BALANCE_WARN = "balance_warn"


def is_silent(now: datetime) -> bool:
    """是否落在夜间静默窗口 ``[22:00, 08:00)``（按 ``now`` 的本地时区小时数判）。"""

    return now.hour >= _SILENT_START_HOUR or now.hour < _SILENT_END_HOUR


def parse_offsets(raw: str | None) -> tuple[int, ...]:
    """把 ``'1440,60'`` 解析成去重、正整数的分钟数元组；空串/非法值 → 空（不发提醒）。"""

    if not raw:
        return ()
    offsets: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            continue
        if value > 0 and value not in offsets:
            offsets.append(value)
    return tuple(offsets)


async def lesson_reminder_offsets(db: AsyncSession, tenant_id: UUID) -> tuple[int, ...]:
    """机构的上课提醒提前分钟数；无 org_config 行回落默认，空串/非法 → 空（不发提醒）。"""

    value = await db.scalar(
        select(OrgConfig.lesson_reminder_hours).where(OrgConfig.tenant_id == tenant_id)
    )
    if value is None:
        return DEFAULT_LESSON_REMINDER_OFFSETS
    return parse_offsets(value)


async def balance_alert_threshold(db: AsyncSession, tenant_id: UUID) -> int:
    """机构的余额预警阈值；无 org_config 行回落默认 5。"""

    value = await db.scalar(
        select(OrgConfig.balance_alert_threshold).where(OrgConfig.tenant_id == tenant_id)
    )
    return int(value) if value is not None else DEFAULT_BALANCE_ALERT_THRESHOLD


def _format_time(value: datetime) -> str:
    return value.astimezone(BUSINESS_TZ).strftime("%m月%d日 %H:%M")


async def _active_tenant_ids(db: AsyncSession, today: date) -> list[UUID]:
    """未到期租户 id（``end_date >= today``）。到期租户不再产生新的通知事件（7.2）。

    到期判定与写闸门同口径：``today > end_date`` 才算到期，``end_date`` 当天仍可用。
    已 enqueue 的 notification 不动（由 6.1 引擎照常消化），这里只拦「新扫描入队」。
    """

    return list(
        (
            await db.scalars(
                select(Tenant.id)
                .join(TenantSubscription, TenantSubscription.tenant_id == Tenant.id)
                .where(TenantSubscription.end_date >= today)
            )
        ).all()
    )


def _lesson_reminder_payload(
    *,
    parent_id: UUID,
    student_id: UUID,
    lesson_id: UUID,
    student_name: str,
    class_name: str,
    start_str: str,
) -> dict:
    body = f"{student_name} 将于 {start_str} 上《{class_name}》"
    return {
        "parent_ids": [str(parent_id)],
        "template_scene": SCENE_LESSON_REMIND,
        "title": "上课提醒",
        "body": body,
        "content": f"【上课提醒】{body}，请准时参加。",
        # 微信订阅模板字段映射（thing1/thing2/thing3 为占位，字段名等微信后台定稿）。
        "data": {
            "thing1": {"value": class_name},
            "thing2": {"value": start_str},
            "thing3": {"value": student_name},
        },
        "source_type": EVENT_LESSON_REMINDER,
        "source_id": str(lesson_id),
        "student_id": str(student_id),
    }


def _balance_alert_payload(
    *,
    parent_id: UUID,
    student_id: UUID,
    student_name: str,
    balance: int,
    source_id: str,
) -> dict:
    body = f"{student_name} 剩余 {balance} 课时，请及时续费"
    return {
        "parent_ids": [str(parent_id)],
        "template_scene": SCENE_BALANCE_WARN,
        "title": "课时余额提醒",
        "body": body,
        "content": f"【课时余额提醒】{body}。",
        "data": {
            "thing1": {"value": student_name},
            "thing2": {"value": f"剩余 {balance} 课时"},
        },
        "source_type": EVENT_BALANCE_ALERT,
        "source_id": source_id,
        "student_id": str(student_id),
    }


async def scan_lesson_reminders(db_factory: SessionFactory, *, now: datetime | None = None) -> int:
    """一轮上课提醒扫描：返回本次入队的通知条数（按家长拆分后）。

    每个提前 offset 是独立时点，只在它自己的窗口内入队：offset 按分钟数从大到小排，
    第 ``i`` 个 offset 的窗口是 ``[start_at - offset_i, start_at - offset_{i+1})``
    （最小 offset 的上界是 ``start_at``，课开始即不再提醒）。这样「提前 1 天」的提醒
    一旦「提前 1 小时」到点就过期、不再补发；但若某时点落在夜间静默窗口，只要还没被
    更小的时点接管，静默结束后下一轮扫描仍会捡起（dedupe 防重）。cancelled / moved
    课次不在此列（``status='normal'`` 过滤）。
    """

    now = now or datetime.now(BUSINESS_TZ)
    if is_silent(now):
        return 0

    enqueued = 0
    async with db_factory() as db:
        async with db.begin():
            tenant_ids = await _active_tenant_ids(db, now.astimezone(BUSINESS_TZ).date())
            for tenant_id in tenant_ids:
                offsets = sorted(await lesson_reminder_offsets(db, tenant_id), reverse=True)
                if not offsets:
                    continue
                for i, offset in enumerate(offsets):
                    next_offset = offsets[i + 1] if i + 1 < len(offsets) else None
                    upper = now + timedelta(minutes=next_offset) if next_offset is not None else now
                    lessons = (
                        await db.scalars(
                            select(Lesson).where(
                                Lesson.tenant_id == tenant_id,
                                Lesson.status == "normal",
                                Lesson.start_at > upper,
                                Lesson.start_at <= now + timedelta(minutes=offset),
                            )
                        )
                    ).all()
                    for lesson in lessons:
                        enqueued += await _enqueue_lesson_reminders(db, lesson, offset)
    return enqueued


async def _enqueue_lesson_reminders(db: AsyncSession, lesson: Lesson, offset: int) -> int:
    """给一课次的每个在读学生、每个绑定家长各入队一条提醒（按家长拆分）。

    ``offset`` 是提前分钟数，进 dedupe_key——提前 1 天与提前 1 小时是两个独立提醒，
    各自要发一次，不能共用同一幂等键（0.7 的单 offset 设计在这里拆成两个时点）。
    """

    class_name = await db.scalar(select(Class.name).where(Class.id == lesson.class_id))
    student_ids = (
        await db.scalars(
            select(ClassEnrollment.student_id).where(ClassEnrollment.class_id == lesson.class_id)
        )
    ).all()
    if not student_ids:
        return 0

    names = {
        row.id: row.name
        for row in (await db.scalars(select(Student).where(Student.id.in_(student_ids)))).all()
    }
    bindings = (
        await db.execute(
            select(ParentStudent.student_id, ParentStudent.parent_id).where(
                ParentStudent.student_id.in_(student_ids)
            )
        )
    ).all()

    start_str = _format_time(lesson.start_at)
    enqueued = 0
    for student_id, parent_id in bindings:
        dedupe_key = (
            f"{EVENT_LESSON_REMINDER}:{lesson.tenant_id}:{lesson.id}"
            f":{student_id}:{parent_id}:{offset}"
        )
        created = await enqueue_notification(
            db,
            tenant_id=lesson.tenant_id,
            event_type=EVENT_LESSON_REMINDER,
            dedupe_key=dedupe_key,
            payload=_lesson_reminder_payload(
                parent_id=parent_id,
                student_id=student_id,
                lesson_id=lesson.id,
                student_name=names.get(student_id, "孩子"),
                class_name=class_name or "课程",
                start_str=start_str,
            ),
            channels=CHANNEL_CHAIN,
        )
        if created:
            enqueued += 1
    return enqueued


async def scan_balance_alerts(db_factory: SessionFactory, *, now: datetime | None = None) -> int:
    """一轮余额预警扫描：返回本次入队的通知条数（按家长拆分后）。

    阈值 > 0 的机构里，课时余额（``SUM(delta)``）低于阈值的学生，按「最近一条 consume
    流水 id（无 consume 用 ``init``）」生成 dedupe_key——每消费一课时 key 变化、可再提醒；
    两次课之间每小时扫描不会重复轰炸。余额一律以 ledger 现算，禁止另算。
    """

    now = now or datetime.now(BUSINESS_TZ)
    if is_silent(now):
        return 0

    enqueued = 0
    async with db_factory() as db:
        async with db.begin():
            tenant_ids = await _active_tenant_ids(db, now.astimezone(BUSINESS_TZ).date())
            for tenant_id in tenant_ids:
                threshold = await balance_alert_threshold(db, tenant_id)
                if threshold <= 0:
                    continue
                low_rows = (
                    await db.execute(
                        select(
                            LessonLedger.student_id,
                            func.sum(LessonLedger.delta).label("balance"),
                        )
                        .where(LessonLedger.tenant_id == tenant_id)
                        .group_by(LessonLedger.student_id)
                        .having(func.sum(LessonLedger.delta) < threshold)
                    )
                ).all()
                for student_id, balance in low_rows:
                    enqueued += await _enqueue_balance_alerts(
                        db, tenant_id, student_id, int(balance)
                    )
    return enqueued


async def _enqueue_balance_alerts(
    db: AsyncSession, tenant_id: UUID, student_id: UUID, balance: int
) -> int:
    """给一个低余额学生的每个绑定家长各入队一条预警（按家长拆分）。"""

    student_name = await db.scalar(select(Student.name).where(Student.id == student_id))
    latest_consume = await db.scalar(
        select(LessonLedger.id)
        .where(LessonLedger.student_id == student_id, LessonLedger.type == "consume")
        .order_by(LessonLedger.created_at.desc(), LessonLedger.id.desc())
        .limit(1)
    )
    consume_key = str(latest_consume) if latest_consume else "init"
    # in_app 兜底的幂等键 source_id：有 consume 用 consume id，无 consume 用 student id。
    source_id = str(latest_consume) if latest_consume else str(student_id)
    parent_ids = (
        await db.scalars(
            select(ParentStudent.parent_id).where(ParentStudent.student_id == student_id)
        )
    ).all()

    enqueued = 0
    for parent_id in parent_ids:
        dedupe_key = f"{EVENT_BALANCE_ALERT}:{student_id}:{consume_key}:{parent_id}"
        created = await enqueue_notification(
            db,
            tenant_id=tenant_id,
            event_type=EVENT_BALANCE_ALERT,
            dedupe_key=dedupe_key,
            payload=_balance_alert_payload(
                parent_id=parent_id,
                student_id=student_id,
                student_name=student_name or "孩子",
                balance=balance,
                source_id=source_id,
            ),
            channels=CHANNEL_CHAIN,
        )
        if created:
            enqueued += 1
    return enqueued
