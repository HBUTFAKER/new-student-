"""Parent self-service endpoints (0.5 section 9)."""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPrincipal, DbSession
from app.api.permissions import get_parent_scope
from app.core.auth import Principal
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.tenant import tenant_context
from app.schemas.ledger import LedgerType
from app.schemas.me import (
    ActiveStudentRequest,
    ActiveStudentResponse,
    MeBalancePackage,
    MeBalanceResponse,
    MeBindingRequest,
    MeBindingResponse,
    MeLedgerItem,
    MeLedgerResponse,
    MeNotificationItem,
    MeNotificationList,
    MeReadRequest,
    MeReadResponse,
    MeScheduleItem,
    MeScheduleResponse,
    MeStudentItem,
    MeStudentList,
    MeUnreadCount,
    SubscribeAuthItem,
    SubscribeAuthRequest,
    SubscribeAuthResponse,
    SubscribeTemplateItem,
    SubscribeTemplateList,
)
from app.schemas.subscription import SubscriptionStatusResponse
from app.services.auth import switch_active_student, wechat_bindings
from app.services.bindings import redeem_invite_code
from app.services.notification import (
    mark_read,
    message_page,
    parents_of_wechat_user,
    unread_count,
)
from app.services.portal import (
    child_balance,
    child_ledger,
    child_schedule,
    resolve_schedule_window,
)
from app.services.subscribe import record_subscribe_auths
from app.services.subscription import get_subscription_status, owner_session

router = APIRouter(prefix="/me", tags=["me"])

FromQuery = Annotated[datetime, Query(alias="from", description="窗口起点，含，ISO 8601 带时区")]
ToQuery = Annotated[datetime, Query(alias="to", description="窗口终点，不含，ISO 8601 带时区")]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
NotificationPageSize = Annotated[int, Query(ge=1, le=50, description="每页条数，默认 20")]


@router.post(
    "/bindings",
    response_model=MeBindingResponse,
    status_code=201,
    summary="家长凭邀请码绑定孩子",
)
async def bind_child(
    payload: MeBindingRequest, db: DbSession, principal: CurrentPrincipal
) -> MeBindingResponse:
    redeemed = await redeem_invite_code(db, principal, payload.invite_code)
    return MeBindingResponse(
        student_id=redeemed.student_id,
        student_name=redeemed.student_name,
        relation=redeemed.relation,
        access_token=redeemed.pair.access_token,
        refresh_token=redeemed.pair.refresh_token,
        expires_in=redeemed.pair.expires_in,
        tenant_id=redeemed.pair.tenant_id,  # type: ignore[arg-type]
        active_student_id=redeemed.pair.active_student_id,  # type: ignore[arg-type]
    )


@router.get("/students", response_model=MeStudentList, summary="家长绑定孩子列表")
async def my_students(db: DbSession, principal: CurrentPrincipal) -> MeStudentList:
    if principal.role != "parent" or principal.subject_id is None:
        raise AppError(403, "OUT_OF_SCOPE", "仅家长可访问")

    # Cross-tenant by design, but only for the caller's own wechat_user_id and
    # only identity fields (0.6 controlled query). No business detail leaks.
    bindings = await wechat_bindings(db, principal.subject_id)
    seen: set[tuple] = set()
    items: list[MeStudentItem] = []
    for binding in bindings:
        key = (binding.tenant_id, binding.student_id)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            MeStudentItem(
                student_id=binding.student_id,
                student_name=binding.student_name,
                tenant_id=binding.tenant_id,
                tenant_name=binding.tenant_name,
                relation=binding.relation,
                active=(
                    binding.tenant_id == principal.tenant_id
                    and binding.student_id == principal.active_student_id
                ),
            )
        )
    return MeStudentList(items=items)


@router.post(
    "/active-student",
    response_model=ActiveStudentResponse,
    summary="切换当前孩子并重签 JWT",
)
async def set_active_student(
    payload: ActiveStudentRequest, db: DbSession, principal: CurrentPrincipal
) -> ActiveStudentResponse:
    pair = await switch_active_student(db, principal, payload.student_id)
    return ActiveStudentResponse(
        active_student_id=pair.active_student_id,  # type: ignore[arg-type]
        tenant_id=pair.tenant_id,  # type: ignore[arg-type]
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


async def child_session(db: DbSession, principal: CurrentPrincipal) -> AsyncIterator[AsyncSession]:
    """家长端只读三件套的统一入口：角色 → 当前孩子 → 绑定集合实时复核。

    三道闸，缺一不可：

    1. 非家长（老师/教务/管理员）一律 403 —— 032 红线「teacher/admin 访问 /me/* 403」；
    2. 没有激活孩子（未绑定 / 多孩子未选）→ 403 ``OUT_OF_SCOPE``（不是
       ``TENANT_CONTEXT_REQUIRED``，家长端只认这一个码，小程序按它跳选择孩子页）；
    3. 激活孩子必须仍在**实时**绑定集合里（token 里的 ``active_student_id`` 过期或被解绑时
       立刻失效），然后才开租户事务让 RLS 兜住跨租户。
    """

    if principal.role != "parent" or principal.subject_id is None:
        raise AppError(403, "OUT_OF_SCOPE", "仅家长可访问")
    if principal.active_student_id is None or principal.tenant_id is None:
        raise AppError(403, "OUT_OF_SCOPE", "请先选择孩子")

    scope = await get_parent_scope(db, principal)
    if scope.active is None or scope.active.student_id != principal.active_student_id:
        raise AppError(403, "OUT_OF_SCOPE", "当前孩子不在绑定集合内")

    # get_parent_scope 的查询开了隐式事务，tenant_context 要自己 begin，先回滚掉。
    await db.rollback()
    async with tenant_context(db, principal.tenant_id):
        yield db


ChildSession = Annotated[AsyncSession, Depends(child_session)]


async def parent_tenant_session(
    db: DbSession, principal: CurrentPrincipal
) -> AsyncIterator[AsyncSession]:
    """订阅授权用的**机构级**闸门（比 ``child_session`` 少一道「激活孩子」）。

    授权记录挂在 ``(tenant_id, wechat_user_id, template_id)`` 上，跟具体孩子无关，
    所以只要求「家长 + 当前机构在实时绑定集合里」，不要求选中某个孩子。
    但机构不能凭空来：token 里的 ``tenant_id`` 必须仍能在绑定集合里找到，
    否则就是换了机构的旧 token，一律 403 ``OUT_OF_SCOPE``。
    """

    if principal.role != "parent" or principal.subject_id is None:
        raise AppError(403, "OUT_OF_SCOPE", "仅家长可访问")
    if principal.tenant_id is None:
        raise AppError(403, "OUT_OF_SCOPE", "请先绑定并选择孩子")

    scope = await get_parent_scope(db, principal)
    if not any(binding.tenant_id == principal.tenant_id for binding in scope.bindings):
        raise AppError(403, "OUT_OF_SCOPE", "当前机构不在绑定集合内")

    await db.rollback()
    async with tenant_context(db, principal.tenant_id):
        yield db


ParentTenantSession = Annotated[AsyncSession, Depends(parent_tenant_session)]


def _active_student_id(principal: Principal) -> UUID:
    """``child_session`` 已保证非空，这里只是把 Optional 收窄成 UUID。"""

    student_id = principal.active_student_id
    if student_id is None:
        raise AppError(403, "OUT_OF_SCOPE", "请先选择孩子")
    return student_id


@router.get("/schedule", response_model=MeScheduleResponse, summary="当前孩子课表")
async def my_schedule(
    db: ChildSession,
    principal: CurrentPrincipal,
    date_from: FromQuery,
    date_to: ToQuery,
) -> MeScheduleResponse:
    """§9.3：窗口内课次，``cancelled`` 不返回；``makeup`` 来自补课席位。"""

    student_id = _active_student_id(principal)
    start, end = resolve_schedule_window(date_from, date_to)
    entries = await child_schedule(db, student_id, start, end)
    return MeScheduleResponse(
        student_id=student_id,
        from_=start,
        to=end,
        items=[
            MeScheduleItem(
                lesson_id=entry.lesson_id,
                class_name=entry.class_name,
                subject=entry.subject,
                teacher_name=entry.teacher_name,
                start_at=entry.start_at,
                end_at=entry.end_at,
                status=entry.status,
            )
            for entry in entries
        ],
    )


@router.get("/balance", response_model=MeBalanceResponse, summary="当前孩子课时余额")
async def my_balance(db: ChildSession, principal: CurrentPrincipal) -> MeBalanceResponse:
    """§9.4：``total`` 实时 SUM(delta)，负数照实返回；包列表只含仍有效的包。"""

    student_id = _active_student_id(principal)
    total, packages = await child_balance(db, student_id)
    return MeBalanceResponse(
        student_id=student_id,
        total=total,
        packages=[
            MeBalancePackage(
                purchase_id=item.purchase_id,
                remaining=item.remaining,
                expires_at=item.expires_at,
            )
            for item in packages
        ],
    )


@router.get("/ledger", response_model=MeLedgerResponse, summary="当前孩子扣课明细")
async def my_ledger(
    db: ChildSession,
    principal: CurrentPrincipal,
    type: LedgerType | None = None,
    page: Page = 1,
    page_size: PageSize = 20,
) -> MeLedgerResponse:
    """§9.5：该孩子全部类型流水（``type`` 可筛），分页默认 20 上限 100。"""

    student_id = _active_student_id(principal)
    items, total = await child_ledger(
        db, student_id, ledger_type=type, page=page, page_size=page_size
    )
    return MeLedgerResponse(
        items=[
            MeLedgerItem(
                type=item.type,
                delta=item.delta,
                class_name=item.class_name,
                created_at=item.created_at,
            )
            for item in items
        ],
        total=total,
    )


@router.get(
    "/subscribe-templates",
    response_model=SubscribeTemplateList,
    summary="订阅消息模板列表（后端配置下发）",
)
async def my_subscribe_templates(principal: CurrentPrincipal) -> SubscribeTemplateList:
    """5.4：给 ``wx.requestSubscribeMessage`` 用的模板 id 全从这里拿，小程序不硬编码。

    不要求选孩子：登录后的家长就能读，模板本身不含任何业务数据。
    """

    if principal.role != "parent" or principal.subject_id is None:
        raise AppError(403, "OUT_OF_SCOPE", "仅家长可访问")

    templates = get_settings().wechat_subscribe_templates
    return SubscribeTemplateList(
        items=[
            SubscribeTemplateItem(
                template_id=template.template_id,
                type=template.type,
                scene=template.scene,
            )
            for template in templates
        ]
    )


@router.post(
    "/subscribe-auths",
    response_model=SubscribeAuthResponse,
    summary="上报订阅授权结果（只收授权，不发消息）",
)
async def save_subscribe_auths(
    payload: SubscribeAuthRequest,
    db: ParentTenantSession,
    principal: CurrentPrincipal,
) -> SubscribeAuthResponse:
    """5.4：``accept`` 累加额度，``reject``/``ban`` 只改状态不清零。

    200 而非 201：语义是 upsert（同一条授权会被反复接受），不是纯创建。
    """

    wechat_user_id = principal.subject_id
    tenant_id = principal.tenant_id
    if wechat_user_id is None or tenant_id is None:
        raise AppError(403, "OUT_OF_SCOPE", "请先绑定并选择孩子")

    allowed = frozenset(
        template.template_id for template in get_settings().wechat_subscribe_templates
    )
    outcomes = await record_subscribe_auths(
        db,
        tenant_id=tenant_id,
        wechat_user_id=wechat_user_id,
        results=payload.results,
        allowed_template_ids=allowed,
    )
    return SubscribeAuthResponse(
        items=[
            SubscribeAuthItem(
                template_id=outcome.template_id,
                status=outcome.status,
                available_count=outcome.available_count,
            )
            for outcome in outcomes
        ],
        total=len(outcomes),
    )


async def _my_parent_ids(db, principal: Principal) -> list[UUID]:
    """当前机构 + 当前微信身份对应的 ``parent`` 行。

    ``parent_tenant_session`` 已保证机构在绑定集合里，这里只把身份落到业务主键上：
    消息按 ``parent_id`` 存，读侧必须走同一把钥匙，body / query 里的任何家长标识都不认。
    """

    wechat_user_id = principal.subject_id
    if wechat_user_id is None:
        raise AppError(403, "OUT_OF_SCOPE", "仅家长可访问")
    parent_ids = await parents_of_wechat_user(db, wechat_user_id)
    if not parent_ids:
        # 机构在绑定集合里但没有对应 parent 行：数据不完整，按越权处理，别返回空列表。
        raise AppError(403, "OUT_OF_SCOPE", "仅家长可访问")
    return parent_ids


@router.get("/notifications", response_model=MeNotificationList, summary="站内信列表")
async def my_notifications(
    db: ParentTenantSession,
    principal: CurrentPrincipal,
    page: Page = 1,
    page_size: NotificationPageSize = 20,
) -> MeNotificationList:
    """5.5：按 ``created_at`` 倒序，``unread`` 与列表同源，红点不用再算一遍。"""

    parent_ids = await _my_parent_ids(db, principal)
    rows, total, unread = await message_page(
        db, parent_ids=parent_ids, page=page, page_size=page_size
    )
    return MeNotificationList(
        items=[
            MeNotificationItem(
                id=row.id,
                type=row.type,
                title=row.title,
                body=row.body,
                student_id=row.student_id,
                read=row.read_at is not None,
                created_at=row.created_at,
            )
            for row in rows
        ],
        total=total,
        unread=unread,
    )


@router.get(
    "/notifications/unread-count",
    response_model=MeUnreadCount,
    summary="未读数（红点）",
)
async def my_unread_count(db: ParentTenantSession, principal: CurrentPrincipal) -> MeUnreadCount:
    """轻量：只 count，不 join 不取行；首页 onShow 每次拉一下够用，不做轮询。"""

    parent_ids = await _my_parent_ids(db, principal)
    return MeUnreadCount(unread=await unread_count(db, parent_ids=parent_ids))


@router.post("/notifications/read", response_model=MeReadResponse, summary="标记已读")
async def mark_notifications_read(
    payload: MeReadRequest,
    db: ParentTenantSession,
    principal: CurrentPrincipal,
) -> MeReadResponse:
    """``ids`` 为空 = 全部已读；跨家长 / 跨租户的 id 整批 404，不落任何更新。"""

    parent_ids = await _my_parent_ids(db, principal)
    marked = await mark_read(db, parent_ids=parent_ids, ids=payload.ids)
    return MeReadResponse(marked=marked)


@router.get(
    "/subscription",
    response_model=SubscriptionStatusResponse,
    summary="订阅状态（前端横幅数据源）",
)
async def my_subscription(principal: CurrentPrincipal) -> SubscriptionStatusResponse:
    """7.2：任意机构内角色可调；家长端不做横幅但本接口不限制。读的是平台层订阅行。"""

    tenant_id = principal.tenant_id
    if tenant_id is None:
        raise AppError(403, "OUT_OF_SCOPE", "仅机构账号可访问")
    async with owner_session() as db:
        status = await get_subscription_status(db, tenant_id)
    return SubscriptionStatusResponse(
        plan_name=status.plan_name,
        end_date=status.end_date,
        days_left=status.days_left,
        expired=status.expired,
    )
