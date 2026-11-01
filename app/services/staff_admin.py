"""Staff account administration: create/revive, guarded update, list, self-deactivate.

US-301 补齐 + 停用/注销/复号。零新表零加列：完全复用 ``staff_account`` 既有列
（``status`` / ``role`` / ``teacher_id`` / ``password_hash`` / ``name`` / ``phone``）。

复号复活口径（发令部拍板）：同 ``(tenant_id, phone)`` 已有 ``inactive`` 账号时，
再次创建 = 复活该行（覆盖 name/password_hash/role/teacher_id 并置回 active），
不是新建第二行，也不是硬删。``active`` 则 409 ``PHONE_IN_USE``。
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal
from app.core.errors import AppError
from app.core.security import hash_password, verify_password
from app.db.tenant import tenant_context
from app.models.archive import Teacher
from app.models.org import StaffAccount

#: 本接口管辖的租户内角色；``platform_admin`` 不在管辖（跨租户 404）。
_TENANT_ROLES = ("admin", "registrar", "teacher")


async def _validate_teacher_ref(
    db: AsyncSession, *, tenant_id: UUID, role: str, teacher_id: UUID | None
) -> None:
    """Role ↔ teacher archive binding rules (same口径 as provisioning).

    * ``teacher`` 必须带 ``teacher_id``；``registrar``/``admin`` 必须为空；
    * 非空的 ``teacher_id`` 必须指向本租户真实存在的老师档案（跨租户 → 404）。
    """

    if role == "teacher":
        if teacher_id is None:
            raise AppError(422, "TEACHER_REQUIRED", "老师角色必须关联老师档案")
    elif teacher_id is not None:
        raise AppError(422, "TEACHER_NOT_ALLOWED", "非老师角色不能关联老师档案")

    if teacher_id is not None:
        teacher = await db.get(Teacher, teacher_id)
        if teacher is None or teacher.tenant_id != tenant_id:
            raise AppError(404, "TEACHER_NOT_FOUND", "老师档案不存在")


async def _active_admin_count(db: AsyncSession, *, tenant_id: UUID) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(StaffAccount)
            .where(
                StaffAccount.tenant_id == tenant_id,
                StaffAccount.role == "admin",
                StaffAccount.status == "active",
            )
        )
        or 0
    )


@dataclass(frozen=True, slots=True)
class StaffCreateResult:
    account: StaffAccount
    revived: bool


async def create_staff_account(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    name: str,
    phone: str,
    role: str,
    password: str,
    teacher_id: UUID | None,
) -> StaffCreateResult:
    """Create an account, or revive the inactive row sharing ``(tenant_id, phone)``."""

    await _validate_teacher_ref(db, tenant_id=tenant_id, role=role, teacher_id=teacher_id)

    existing = await db.scalar(
        select(StaffAccount).where(
            StaffAccount.tenant_id == tenant_id,
            StaffAccount.phone == phone,
        )
    )
    if existing is not None:
        if existing.status == "active":
            raise AppError(409, "PHONE_IN_USE", "该手机号已有启用中的员工账号")
        # 复活：覆盖请求里的全部字段并置回 active（同手机号回炉复活规则）。
        existing.name = name
        existing.password_hash = hash_password(password)
        existing.role = role
        existing.teacher_id = teacher_id
        existing.status = "active"
        await db.flush()
        return StaffCreateResult(account=existing, revived=True)

    account = StaffAccount(
        tenant_id=tenant_id,
        name=name,
        phone=phone,
        password_hash=hash_password(password),
        role=role,
        teacher_id=teacher_id,
        status="active",
    )
    db.add(account)
    await db.flush()
    return StaffCreateResult(account=account, revived=False)


async def update_staff_account(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    actor_id: UUID,
    staff_id: UUID,
    changes: dict,
) -> StaffAccount:
    """Partial update with the two guardrails (LAST_ADMIN before CANNOT_DISABLE_SELF).

    ``LAST_ADMIN`` 先判：停用/降级（含改自己）最后一个 active admin 时，机构会没有
    管理员，保护级别高于「不能停用自己」，所以先拦。非唯一 admin 停用自己才落到
    ``CANNOT_DISABLE_SELF``。
    """

    target = await db.scalar(
        select(StaffAccount).where(
            StaffAccount.id == staff_id,
            StaffAccount.tenant_id == tenant_id,
        )
    )
    if target is None:
        raise AppError(404, "STAFF_NOT_FOUND", "员工账号不存在")

    new_status = changes.get("status", target.status)
    new_role = changes.get("role", target.role)
    new_teacher_id = changes.get("teacher_id", target.teacher_id)

    # 失去 admin 身份 = 停用（active→inactive）或降级（admin→teacher/registrar）。
    loses_admin = (
        target.status == "active"
        and target.role == "admin"
        and (new_status == "inactive" or new_role in ("registrar", "teacher"))
    )
    if loses_admin and await _active_admin_count(db, tenant_id=tenant_id) <= 1:
        raise AppError(403, "LAST_ADMIN", "不能停用或降级本机构最后一个管理员")

    if new_status == "inactive" and target.id == actor_id:
        raise AppError(403, "CANNOT_DISABLE_SELF", "不能停用自己")

    # 只在 role / teacher_id 变化时重校验关联（纯 status/name/password 不碰档案）。
    if "role" in changes or "teacher_id" in changes:
        await _validate_teacher_ref(
            db, tenant_id=tenant_id, role=new_role, teacher_id=new_teacher_id
        )

    if "name" in changes:
        target.name = changes["name"]
    if "role" in changes:
        target.role = new_role
    if "teacher_id" in changes:
        target.teacher_id = new_teacher_id
    if "status" in changes:
        target.status = new_status
    if "password" in changes:
        target.password_hash = hash_password(changes["password"])

    await db.flush()
    return target


async def list_staff_accounts(
    db: AsyncSession, *, tenant_id: UUID, page: int, page_size: int
) -> tuple[list[StaffAccount], int]:
    stmt = select(StaffAccount).where(StaffAccount.tenant_id == tenant_id)
    total = await db.scalar(
        select(func.count()).select_from(StaffAccount).where(StaffAccount.tenant_id == tenant_id)
    )
    rows = await db.scalars(
        stmt.order_by(StaffAccount.created_at.desc(), StaffAccount.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(rows), int(total or 0)


async def self_deactivate(db: AsyncSession, principal: Principal, password: str) -> None:
    """Set the caller's own account to ``inactive`` after verifying the password."""

    if principal.role in ("parent", "platform_admin"):
        raise AppError(403, "OUT_OF_SCOPE", "当前角色不能注销账号")
    if principal.subject_id is None or principal.tenant_id is None:
        raise AppError(403, "OUT_OF_SCOPE", "当前身份不能注销账号")

    # 读自己的 staff 需要租户上下文让 RLS 放行；tenant_context 退出时自动 commit。
    async with tenant_context(db, principal.tenant_id):
        target = await db.scalar(
            select(StaffAccount).where(StaffAccount.id == principal.subject_id)
        )
        if target is None or not verify_password(password, target.password_hash):
            raise AppError(401, "AUTH_FAILED", "手机号或密码错误")

        if target.role == "admin" and target.status == "active":
            if await _active_admin_count(db, tenant_id=principal.tenant_id) <= 1:
                raise AppError(403, "LAST_ADMIN", "不能注销本机构最后一个管理员")

        target.status = "inactive"
        await db.flush()
