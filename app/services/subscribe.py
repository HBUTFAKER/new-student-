"""Subscription-message authorisation records (5.4 collects, 6.2 sends).

额度语义（6.2 全靠这两列，别改）：

- ``accept``：按 ``(tenant_id, wechat_user_id, template_id)`` upsert，
  ``available_count += 1``、``status='authorized'``。微信一次性订阅的语义就是
  「每接受一次 +1 次可发送额度」，重复接受继续累加。
- ``reject`` / ``ban``：只改 ``status``，**不动** ``available_count`` —— 之前攒下的
  额度依然有效，家长这次点拒绝不等于把历史额度作废。
- ``updated_at``：最近一次授权动作时间，6.2 判断「这条授权还新鲜吗」。

写入用 ``INSERT ... ON CONFLICT DO UPDATE`` 一条语句完成：并发双击同一个模板时
不会出现「先查后写」的竞态，也不会像排课 EXCLUDE 那样被判死锁。
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.wechat import SubscribeAuth
from app.schemas.me import SubscribeAuthResult
from app.services.ledger import BUSINESS_TZ

ACCEPT = "accept"
REJECT = "reject"
BAN = "ban"

#: 微信弹窗结果 → 落库状态。``ban`` 是「总是保持以上选择」，等同于长期拒绝。
STATUS_BY_RESULT: dict[str, str] = {
    ACCEPT: "authorized",
    REJECT: "rejected",
    BAN: "banned",
}


@dataclass(frozen=True, slots=True)
class AuthOutcome:
    """一条授权记录写入后的最新状态。"""

    template_id: str
    status: str
    available_count: int


def quota_delta(result_status: str) -> int:
    """只有 accept 攒额度；reject/ban 一律 0。"""

    return 1 if result_status == ACCEPT else 0


async def record_subscribe_auths(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    wechat_user_id: UUID,
    results: list[SubscribeAuthResult],
    allowed_template_ids: frozenset[str],
) -> list[AuthOutcome]:
    """把一次批量授权结果落库，返回每条的最新状态。

    未知模板在写之前整批拒绝（422），避免「一半写进去、一半报错」留下脏数据。
    ``wechat_user_id`` / ``tenant_id`` 由调用方从 token 身份解析，本函数不接受
    任何客户端传来的身份字段。
    """

    unknown = sorted(
        {item.template_id for item in results if item.template_id not in allowed_template_ids}
    )
    if unknown:
        raise AppError(
            422,
            "SUBSCRIBE_TEMPLATE_UNKNOWN",
            "模板不在后端配置里：" + "、".join(unknown),
        )

    table = SubscribeAuth.__table__
    now = datetime.now(BUSINESS_TZ)
    outcomes: list[AuthOutcome] = []
    for item in results:
        status = STATUS_BY_RESULT[item.status]
        delta = quota_delta(item.status)
        statement = (
            pg_insert(SubscribeAuth)
            .values(
                tenant_id=tenant_id,
                wechat_user_id=wechat_user_id,
                template_id=item.template_id,
                status=status,
                available_count=delta,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[
                    table.c.tenant_id,
                    table.c.wechat_user_id,
                    table.c.template_id,
                ],
                set_={
                    "status": status,
                    # 已有额度 + 本次增量（reject/ban 的 delta 是 0，等于不改）
                    "available_count": table.c.available_count + delta,
                    "updated_at": now,
                },
            )
            .returning(table.c.template_id, table.c.status, table.c.available_count)
        )
        row = (await db.execute(statement)).one()
        outcomes.append(
            AuthOutcome(
                template_id=row.template_id,
                status=row.status,
                available_count=row.available_count,
            )
        )
    return outcomes
