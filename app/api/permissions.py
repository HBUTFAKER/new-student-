"""Role and data-ownership gates (0.2 gates 2 and 3)."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, DbSession, TenantDbSession
from app.core.auth import Principal
from app.core.errors import AppError
from app.models.course import Class
from app.services.auth import BindingRecord, wechat_bindings


def require_role(*roles: str) -> Callable[[Principal], Principal]:
    """Gate for matrix cells marked ✅ (unconditional for the listed roles)."""

    allowed = frozenset(roles)

    def dependency(principal: CurrentPrincipal) -> Principal:
        if principal.role not in allowed:
            raise AppError(403, "OUT_OF_SCOPE", "当前角色无权执行此操作")
        return principal

    return dependency


OfficeUser = Annotated[Principal, Depends(require_role("admin", "registrar"))]
TeacherOrOffice = Annotated[Principal, Depends(require_role("admin", "registrar", "teacher"))]


def require_any(*roles: str) -> Callable[[Principal], Principal]:
    """Gate for matrix cells marked ⚠️.

    The caller must pair this with an ownership dependency (parent binding or
    teacher class scope); role alone never grants access to every row.
    """

    allowed = frozenset(roles)

    def dependency(principal: CurrentPrincipal) -> Principal:
        if principal.role not in allowed:
            raise AppError(403, "OUT_OF_SCOPE", "当前角色无权执行此操作")
        return principal

    return dependency


@dataclass(frozen=True, slots=True)
class ParentScope:
    """Real-time binding set; never cached in the token."""

    principal: Principal
    bindings: tuple[BindingRecord, ...]
    active: BindingRecord | None

    @property
    def student_ids(self) -> frozenset[UUID]:
        return frozenset(binding.student_id for binding in self.bindings)


async def get_parent_scope(db: DbSession, principal: CurrentPrincipal) -> ParentScope:
    """Resolve the parent's binding set on every request (V-01/V-08)."""

    if principal.role != "parent" or principal.subject_id is None:
        raise AppError(403, "OUT_OF_SCOPE", "仅家长可访问")
    bindings = await wechat_bindings(db, principal.subject_id)
    active = None
    if principal.tenant_id is not None:
        active = next(
            (
                binding
                for binding in bindings
                if binding.tenant_id == principal.tenant_id
                and binding.student_id == principal.active_student_id
            ),
            None,
        )
        if active is None:
            raise AppError(403, "OUT_OF_SCOPE", "当前孩子不在绑定集合内")
    return ParentScope(principal=principal, bindings=tuple(bindings), active=active)


ParentScopeDep = Depends(get_parent_scope)


def ensure_bound_student(scope: ParentScope, student_id: UUID) -> BindingRecord:
    """Object-level guard used by every /me read endpoint."""

    binding = next(
        (item for item in scope.bindings if item.student_id == student_id),
        None,
    )
    if binding is None:
        raise AppError(403, "OUT_OF_SCOPE", "学生不在绑定集合内")
    return binding


@dataclass(frozen=True, slots=True)
class TeacherScope:
    """Teaching-class scope; wired into the archive endpoints in step 1.6."""

    teacher_id: UUID
    class_ids: frozenset[UUID]

    def covers_class(self, class_id: UUID) -> bool:
        return class_id in self.class_ids


async def get_teacher_scope(db: TenantDbSession, principal: CurrentPrincipal) -> TeacherScope:
    """Load the teacher's classes from the tenant context (V-05)."""

    if principal.role != "teacher" or principal.teacher_id is None:
        raise AppError(403, "OUT_OF_SCOPE", "仅老师可访问任教范围")
    class_ids = await db.scalars(select(Class.id).where(Class.teacher_id == principal.teacher_id))
    return TeacherScope(teacher_id=principal.teacher_id, class_ids=frozenset(class_ids.all()))


TeacherScopeDep = Depends(get_teacher_scope)
