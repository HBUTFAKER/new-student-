"""站内信（5.5）：微信订阅消息关掉时的兜底通道。

本步只做最小可用版：**一个写入钩子 + 三个读接口**，不做状态机、不做重试、不做多通道
（那是 6.1 通知中心的活）。写侧只有「签到扣课」一个事件。

三条不变量：

* 消息按 ``parent_id`` 存，不按 ``wechat_user_id`` —— 家长换手机重新登录，历史消息还在。
* 幂等键 ``(source_type, source_id, parent_id)`` 是防重的根，写入一律
  ``INSERT ... ON CONFLICT DO NOTHING``，不做「先查后写」。
* 写入失败不许影响签到：插入在调用方的 SAVEPOINT 里，见 ``attendance._notify_deduct``。
"""

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.archive import Parent, ParentStudent, Student
from app.models.notification import InAppMessage
from app.services.ledger import total_balance

#: 本步唯一的事件类型 / 来源。6.1 加类型时在这里扩常量，不改表。
MESSAGE_TYPE_LESSON_DEDUCT = "lesson_deduct"
SOURCE_TYPE_ATTENDANCE = "attendance"


async def parents_of_wechat_user(db: AsyncSession, wechat_user_id: UUID) -> list[UUID]:
    """当前机构下，这个微信身份对应的 ``parent`` 行（读侧身份链的唯一入口）。"""

    rows = await db.scalars(select(Parent.id).where(Parent.wechat_user_id == wechat_user_id))
    return list(rows.all())


async def record_lesson_deduct(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    student_id: UUID,
    attendance_id: UUID,
    count: int,
) -> int:
    """签到扣课成功后，给该学生的每个绑定家长写一条站内信。

    余额用 ``total_balance`` 现算（扣课流水已在同一事务里落库，读到的就是扣后余额），
    不重算扣包逻辑、不回查 attendance。返回值是「尝试写入的条数」，重复事件被唯一键
    吞掉时实际新增可能更少 —— 这里不需要精确，6.1 也不依赖它。
    """

    student_name = await db.scalar(select(Student.name).where(Student.id == student_id))
    parent_ids = await db.scalars(
        select(ParentStudent.parent_id).where(ParentStudent.student_id == student_id)
    )
    audience = list(parent_ids.all())
    if not audience:
        return 0

    remaining = await total_balance(db, student_id)
    rows = [
        {
            "tenant_id": tenant_id,
            "parent_id": parent_id,
            "student_id": student_id,
            "type": MESSAGE_TYPE_LESSON_DEDUCT,
            "title": f"{student_name or '孩子'} 已签到扣课",
            "body": f"本节课已扣 {count} 课时，剩余 {remaining} 课时",
            "source_type": SOURCE_TYPE_ATTENDANCE,
            "source_id": attendance_id,
        }
        for parent_id in audience
    ]
    await db.execute(
        pg_insert(InAppMessage)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["source_type", "source_id", "parent_id"])
    )
    return len(rows)


async def message_page(
    db: AsyncSession,
    *,
    parent_ids: list[UUID],
    page: int,
    page_size: int,
) -> tuple[list[InAppMessage], int, int]:
    """一页消息 + 总数 + 未读数。列表和红点口径必须同源，所以一起算。"""

    if not parent_ids:
        return [], 0, 0

    scope = InAppMessage.parent_id.in_(parent_ids)
    total = await db.scalar(select(func.count()).select_from(InAppMessage).where(scope))
    unread = await db.scalar(
        select(func.count()).select_from(InAppMessage).where(scope, InAppMessage.read_at.is_(None))
    )
    rows = await db.scalars(
        select(InAppMessage)
        .where(scope)
        .order_by(InAppMessage.created_at.desc(), InAppMessage.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(rows.all()), int(total or 0), int(unread or 0)


async def unread_count(db: AsyncSession, *, parent_ids: list[UUID]) -> int:
    """红点专用：只 count 未读，不 join、不取行。"""

    if not parent_ids:
        return 0
    unread = await db.scalar(
        select(func.count())
        .select_from(InAppMessage)
        .where(InAppMessage.parent_id.in_(parent_ids), InAppMessage.read_at.is_(None))
    )
    return int(unread or 0)


async def mark_read(db: AsyncSession, *, parent_ids: list[UUID], ids: list[UUID]) -> int:
    """把指定消息标已读；``ids`` 为空 = 这个家长的全部未读。

    跨家长 / 跨租户的 id 一律当作不存在：RLS 已经挡住别的机构，剩下的用「查得到才算
    你的」判，缺一个就整批 404，不告诉调用方「哪个 id 存在」。
    """

    if not parent_ids:
        return 0

    scope = InAppMessage.parent_id.in_(parent_ids)
    if ids:
        wanted = set(ids)
        visible = await db.scalars(
            select(InAppMessage.id).where(scope, InAppMessage.id.in_(wanted))
        )
        if set(visible.all()) != wanted:
            raise AppError(404, "MESSAGE_NOT_FOUND", "消息不存在")
        scope = scope & InAppMessage.id.in_(wanted)

    result = await db.execute(
        update(InAppMessage)
        .where(scope, InAppMessage.read_at.is_(None))
        .values(read_at=func.now())
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)
