"""订阅状态与续费（7.2）：到期只读闸门的数据源。

``tenant_subscription`` / ``plan`` 是平台层表，``app_user`` 无 SELECT 权限（0002 迁移
明确不 GRANT），所以这里的查询**必须走 owner 连接**（``migration_database_url``），
与调度器 / worker 共用同一绕过 RLS 的口径。到期判定：``today > end_date`` 才算到期
（``end_date`` 当天仍可用），业务日期一律用 ``business_today()``（UTC+8 固定）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.errors import AppError
from app.models.platform import Plan, TenantSubscription
from app.services.ledger import business_today


def _build_owner_engine():
    """owner 引擎：读平台层表用。test 环境 NullPool，避免连接跨 pytest 事件循环复用。"""

    settings = get_settings()
    options: dict[str, object] = {"pool_pre_ping": True}
    if settings.app_env == "test":
        options["poolclass"] = NullPool
    return create_async_engine(settings.migration_database_url, **options)


@asynccontextmanager
async def owner_session() -> AsyncIterator[AsyncSession]:
    """一个 owner 连接的短会话：给订阅查询 / 续费写操作用。

    每次调用新建 engine + 用完 dispose，不复用跨事件循环的 asyncpg 连接（Windows
    Proactor 的 ``NoneType.send`` 就是跨循环复用踩出来的）。生产写请求 QPS 极低，
    每次一条短连接的开销可接受。
    """

    engine = _build_owner_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            yield db
    finally:
        await engine.dispose()


@dataclass(frozen=True, slots=True)
class SubscriptionStatus:
    plan_name: str
    end_date: date
    days_left: int
    expired: bool


@dataclass(frozen=True, slots=True)
class RenewResult:
    end_date: date
    plan_id: UUID
    plan_name: str


def _is_expired(end_date: date, today: date) -> bool:
    return today > end_date


async def get_subscription_status(db: AsyncSession, tenant_id: UUID) -> SubscriptionStatus:
    """读租户订阅状态。查不到订阅行按 500 暴露（provision_tenant 保证一定有，别静默放行）。"""

    row = (
        await db.execute(
            select(Plan.name, TenantSubscription.end_date)
            .join(TenantSubscription, TenantSubscription.plan_id == Plan.id)
            .where(TenantSubscription.tenant_id == tenant_id)
        )
    ).first()
    if row is None:
        raise AppError(500, "SUBSCRIPTION_MISSING", "机构订阅记录缺失，请联系平台处理")

    plan_name, end_date = row
    today = business_today()
    return SubscriptionStatus(
        plan_name=plan_name,
        end_date=end_date,
        days_left=(end_date - today).days,
        expired=_is_expired(end_date, today),
    )


async def is_subscription_expired(db: AsyncSession, tenant_id: UUID) -> bool:
    """写闸门用的单次判定；查不到订阅行同样按 500 暴露。"""

    return (await get_subscription_status(db, tenant_id)).expired


async def renew_subscription(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    days: int,
    plan_id: UUID | None = None,
) -> RenewResult:
    """手动续费：从 ``max(today, end_date)`` 起顺延 ``days`` 天；带 ``plan_id`` 则同时换套餐。

    已过期机构从今天起算，不是从过期日起算——不白送过期那几天。``plan_id`` 不存在 → 404；
    订阅行不存在 → 404。调用方负责提交事务。
    """

    subscription = await db.scalar(
        select(TenantSubscription).where(TenantSubscription.tenant_id == tenant_id)
    )
    if subscription is None:
        raise AppError(404, "SUBSCRIPTION_NOT_FOUND", "订阅记录不存在")

    next_plan_id = subscription.plan_id
    if plan_id is not None:
        if await db.scalar(select(Plan.id).where(Plan.id == plan_id)) is None:
            raise AppError(404, "PLAN_NOT_FOUND", "套餐不存在")
        next_plan_id = plan_id

    today = business_today()
    subscription.end_date = max(today, subscription.end_date) + timedelta(days=days)
    subscription.plan_id = next_plan_id

    plan_name = await db.scalar(select(Plan.name).where(Plan.id == next_plan_id))
    return RenewResult(
        end_date=subscription.end_date,
        plan_id=next_plan_id,
        plan_name=plan_name or "",
    )
