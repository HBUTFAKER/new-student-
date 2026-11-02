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
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.errors import AppError
from app.models.archive import Student
from app.models.org import StaffAccount
from app.models.platform import Plan, Tenant, TenantSubscription, UsageMeter
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


# ---------------------------------------------------------------- 7.3 套餐限额


#: usage_meter 里消息量的 metric 名（对齐 US-402 设计：metric=messages、period=YYYY-MM）。
MESSAGE_METRIC = "messages"


@dataclass(frozen=True, slots=True)
class PlanLimits:
    max_students: int
    max_messages: int


def current_period() -> str:
    """自然月 period 字符串（``YYYY-MM``），统一按业务时区（UTC+8）算。"""

    today = business_today()
    return f"{today.year:04d}-{today.month:02d}"


async def get_plan_limits(db: AsyncSession, tenant_id: UUID) -> PlanLimits | None:
    """当前生效 plan 的学生/消息限额。查不到订阅/plan → None（调用方决定放行还是报错）。

    plan/tenant_subscription 是平台层表，``db`` 必须是 owner 连接（绕过 RLS）。
    """

    row = (
        await db.execute(
            select(Plan.max_students, Plan.max_messages)
            .join(TenantSubscription, TenantSubscription.plan_id == Plan.id)
            .where(TenantSubscription.tenant_id == tenant_id)
        )
    ).first()
    if row is None:
        return None
    return PlanLimits(max_students=int(row[0]), max_messages=int(row[1]))


async def count_active_students(db: AsyncSession, tenant_id: UUID) -> int:
    """在档（status='active'）学生数。归档/停用不占名额（7.3 口径）。

    ``db`` 必须是 owner 连接（显式 ``tenant_id`` 过滤，绕 RLS）；租户层连接在 RLS 下
    已限到当前租户，也能得出同值，但这里统一走 owner 口径和 ``check_student_limit`` 一致。
    """

    return int(
        await db.scalar(
            select(func.count())
            .select_from(Student)
            .where(Student.tenant_id == tenant_id, Student.status == "active")
        )
        or 0
    )


async def check_student_limit(db: AsyncSession, tenant_id: UUID) -> None:
    """单条新增学生的限额校验：在档数 >= 上限 → 402 ``PLAN_LIMIT_EXCEEDED``。

    只拦「新增」，编辑/转班/续费等既有操作不拦（调用方别往这里塞）。查不到订阅行按
    500 暴露（provision_tenant 保证一定有，不静默放行）。
    """

    limits = await get_plan_limits(db, tenant_id)
    if limits is None:
        raise AppError(500, "SUBSCRIPTION_MISSING", "机构订阅记录缺失，请联系平台处理")
    active = await count_active_students(db, tenant_id)
    if active >= limits.max_students:
        raise AppError(
            402,
            "PLAN_LIMIT_EXCEEDED",
            f"学生数已达套餐上限 {limits.max_students} 人，归档不活跃学生可腾出名额",
        )


async def check_student_bulk_limit(db: AsyncSession, tenant_id: UUID, n: int) -> None:
    """批量导入学生的整批拒绝校验：剩余名额 < 本批人数 → 整批 402（不截断半批入库）。"""

    limits = await get_plan_limits(db, tenant_id)
    if limits is None:
        raise AppError(500, "SUBSCRIPTION_MISSING", "机构订阅记录缺失，请联系平台处理")
    active = await count_active_students(db, tenant_id)
    remaining = limits.max_students - active
    if n > remaining:
        raise AppError(
            402,
            "PLAN_LIMIT_EXCEEDED",
            f"剩余名额 {remaining} 个，本批 {n} 人，请归档学生或联系平台扩容",
        )


async def record_message_sent(db: AsyncSession, tenant_id: UUID, n: int = 1) -> None:
    """当月外发消息成功计数 +n（自然月 period，``metric=messages``）。

    幂等 upsert：``INSERT ... ON CONFLICT (tenant_id, metric, period) DO UPDATE
    value = value + n``。站内信（in_app）不占额，调用方只在「外发通道真正送达」时累加。
    ``db`` 必须是 owner 连接（usage_meter 是平台层表、app_user 无权限）。
    """

    if n <= 0:
        return
    period = current_period()
    await db.execute(
        pg_insert(UsageMeter)
        .values(
            id=uuid4(),
            tenant_id=tenant_id,
            metric=MESSAGE_METRIC,
            value=n,
            period=period,
        )
        .on_conflict_do_update(
            index_elements=["tenant_id", "metric", "period"],
            set_={"value": UsageMeter.value + n},
        )
    )


async def get_month_message_usage(db: AsyncSession, tenant_id: UUID) -> int:
    """当月（自然月）已用消息条数；无记录返回 0。``db`` 必须是 owner 连接。"""

    return int(
        await db.scalar(
            select(UsageMeter.value).where(
                UsageMeter.tenant_id == tenant_id,
                UsageMeter.metric == MESSAGE_METRIC,
                UsageMeter.period == current_period(),
            )
        )
        or 0
    )


async def message_limit_reached(db: AsyncSession, tenant_id: UUID) -> bool:
    """当月消息量是否已达套餐上限。查不到订阅/plan 时返回 False（不限流，别卡死通知）。"""

    limits = await get_plan_limits(db, tenant_id)
    if limits is None:
        return False
    return await get_month_message_usage(db, tenant_id) >= limits.max_messages


# ---------------------------------------------------------------- 7.4 平台后台


@dataclass(frozen=True, slots=True)
class TenantSubscriptionRow:
    """租户订阅一览行（7.4 平台后台列表/详情数据源，读平台层表走 owner 连接）。"""

    tenant_id: UUID
    name: str
    created_at: datetime
    plan_id: UUID
    plan_name: str
    start_date: date
    end_date: date

    @property
    def days_left(self) -> int:
        return (self.end_date - business_today()).days

    @property
    def sub_status(self) -> str:
        """sub_status 三态（复用 7.2/7.3 判定，不发明新状态）：到期 > 试用 > 正常。"""

        if business_today() > self.end_date:
            return "expired"
        if self.plan_name == "试用版":
            return "trial"
        return "active"


def _tenant_subscription_select():
    """tenant ⋈ tenant_subscription ⋈ plan 的 join 列清单（列表与详情共用）。"""

    return (
        select(
            Tenant.id,
            Tenant.name,
            Tenant.created_at,
            TenantSubscription.plan_id,
            Plan.name,
            TenantSubscription.start_date,
            TenantSubscription.end_date,
        )
        .join(TenantSubscription, TenantSubscription.tenant_id == Tenant.id)
        .join(Plan, Plan.id == TenantSubscription.plan_id)
    )


async def list_tenant_subscriptions(
    db: AsyncSession, *, name: str | None = None, page: int = 1, page_size: int = 20
) -> tuple[list[TenantSubscriptionRow], int]:
    """租户订阅列表（7.4）：``name`` 模糊搜索 + 分页，``end_date`` 升序（最先到期在前）。"""

    stmt = _tenant_subscription_select()
    count_stmt = (
        select(func.count())
        .select_from(Tenant)
        .join(TenantSubscription, TenantSubscription.tenant_id == Tenant.id)
        .join(Plan, Plan.id == TenantSubscription.plan_id)
    )
    if name:
        stmt = stmt.where(Tenant.name.ilike(f"%{name}%"))
        count_stmt = count_stmt.where(Tenant.name.ilike(f"%{name}%"))

    total = int(await db.scalar(count_stmt) or 0)
    rows = (
        await db.execute(
            stmt.order_by(TenantSubscription.end_date.asc(), Tenant.created_at.asc(), Tenant.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return [
        TenantSubscriptionRow(
            tenant_id=row[0],
            name=row[1],
            created_at=row[2],
            plan_id=row[3],
            plan_name=row[4],
            start_date=row[5],
            end_date=row[6],
        )
        for row in rows
    ], total


async def get_tenant_subscription_row(
    db: AsyncSession, tenant_id: UUID
) -> TenantSubscriptionRow | None:
    """单租户订阅行；无订阅 → None（调用方按 500 ``SUBSCRIPTION_MISSING`` 暴露）。"""

    row = (await db.execute(_tenant_subscription_select().where(Tenant.id == tenant_id))).first()
    if row is None:
        return None
    return TenantSubscriptionRow(
        tenant_id=row[0],
        name=row[1],
        created_at=row[2],
        plan_id=row[3],
        plan_name=row[4],
        start_date=row[5],
        end_date=row[6],
    )


async def get_tenant_admin_contact(
    db: AsyncSession, tenant_id: UUID
) -> tuple[str | None, str | None]:
    """机构管理员联系方式（role='admin' 且 active，按创建时间取最早一条）。

    ``staff_account`` 是业务层表（有 RLS），这里走 owner 连接显式 ``tenant_id`` 过滤绕 RLS。
    平台运营宿主租户的账号是 ``platform_admin`` 角色，查不到 admin 时返回 ``(None, None)``。
    """

    row = (
        await db.execute(
            select(StaffAccount.name, StaffAccount.phone)
            .where(
                StaffAccount.tenant_id == tenant_id,
                StaffAccount.role == "admin",
                StaffAccount.status == "active",
            )
            .order_by(StaffAccount.created_at.asc(), StaffAccount.id)
            .limit(1)
        )
    ).first()
    if row is None:
        return None, None
    return row[0], row[1]
