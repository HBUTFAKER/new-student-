"""Parent self-binding with a one-time invite code (0.6 decision 3)."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal
from app.core.errors import AppError
from app.models.archive import InviteCode, Parent, ParentStudent, Student
from app.services.auth import TokenPair, token_pair


@dataclass(frozen=True, slots=True)
class RedeemedBinding:
    pair: TokenPair
    student_id: UUID
    student_name: str
    relation: str


async def redeem_invite_code(db: AsyncSession, principal: Principal, code: str) -> RedeemedBinding:
    """Consume code + parent + binding + token in one transaction.

    The invite lookup is a SECURITY DEFINER function because the parent token has
    no tenant yet. Every subsequent statement runs under the tenant parsed from
    the code, so a client cannot choose the tenant.
    """

    if principal.role != "parent" or principal.subject_id is None:
        raise AppError(403, "OUT_OF_SCOPE", "仅家长可自助绑定")

    async with db.begin():
        invite_row = (
            (await db.execute(text("SELECT * FROM auth_invite_code(:code)"), {"code": code}))
            .mappings()
            .first()
        )
        if invite_row is None:
            raise AppError(404, "INVITE_CODE_INVALID", "邀请码无效或已过期")

        await db.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(invite_row["tenant_id"])},
        )
        invite = (
            await db.scalars(
                select(InviteCode).where(InviteCode.id == invite_row["id"]).with_for_update()
            )
        ).one_or_none()
        now = datetime.now(UTC)
        if invite is None or invite.used_at is not None or invite.expires_at <= now:
            raise AppError(404, "INVITE_CODE_INVALID", "邀请码无效或已过期")

        student = await db.get(Student, invite.student_id)
        if student is None or student.tenant_id != invite.tenant_id:
            raise AppError(404, "STUDENT_NOT_FOUND", "学生不存在")
        if student.status == "archived":
            raise AppError(422, "STUDENT_ARCHIVED", "归档学生不可绑定")

        parent = await db.scalar(
            select(Parent)
            .where(
                Parent.tenant_id == invite.tenant_id,
                Parent.wechat_user_id == principal.subject_id,
            )
            .limit(1)
        )
        if parent is None:
            parent = Parent(
                tenant_id=invite.tenant_id,
                name="微信家长",
                phone="",
                wechat_user_id=principal.subject_id,
            )
            db.add(parent)
            await db.flush()

        existing = await db.scalar(
            select(ParentStudent.id).where(
                ParentStudent.parent_id == parent.id,
                ParentStudent.student_id == invite.student_id,
            )
        )
        if existing is not None:
            raise AppError(409, "BINDING_EXISTS", "该孩子已绑定")

        relation = "家长"
        db.add(
            ParentStudent(
                tenant_id=invite.tenant_id,
                parent_id=parent.id,
                student_id=invite.student_id,
                relation=relation,
            )
        )
        invite.used_at = now
        invite.used_by_wechat_user_id = principal.subject_id
        await db.flush()

        pair = token_pair(
            subject_id=principal.subject_id,
            tenant_id=invite.tenant_id,
            role="parent",
            active_student_id=invite.student_id,
        )
        return RedeemedBinding(
            pair=pair,
            student_id=invite.student_id,
            student_name=student.name,
            relation=relation,
        )
