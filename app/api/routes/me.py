"""Parent self-service endpoints (0.5 section 9)."""

from fastapi import APIRouter

from app.api.deps import CurrentPrincipal, DbSession
from app.core.errors import AppError
from app.schemas.me import (
    ActiveStudentRequest,
    ActiveStudentResponse,
    MeBindingRequest,
    MeBindingResponse,
    MeStudentItem,
    MeStudentList,
)
from app.services.auth import switch_active_student, wechat_bindings
from app.services.bindings import redeem_invite_code

router = APIRouter(prefix="/me", tags=["me"])


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
