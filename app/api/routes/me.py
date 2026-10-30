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
    MeScheduleItem,
    MeScheduleResponse,
    MeStudentItem,
    MeStudentList,
)
from app.services.auth import switch_active_student, wechat_bindings
from app.services.bindings import redeem_invite_code
from app.services.portal import (
    child_balance,
    child_ledger,
    child_schedule,
    resolve_schedule_window,
)

router = APIRouter(prefix="/me", tags=["me"])

FromQuery = Annotated[datetime, Query(alias="from", description="窗口起点，含，ISO 8601 带时区")]
ToQuery = Annotated[datetime, Query(alias="to", description="窗口终点，不含，ISO 8601 带时区")]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


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
