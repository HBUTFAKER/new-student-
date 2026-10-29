"""Archive endpoints: students, parents, teachers, classrooms, bindings, invites."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPrincipal, TenantDbSession
from app.api.permissions import OfficeUser, TeacherOrOffice
from app.core.errors import AppError
from app.models.archive import (
    Classroom,
    InviteCode,
    Parent,
    ParentStudent,
    Student,
    Teacher,
)
from app.models.course import Class, ClassEnrollment
from app.models.wechat import WechatUser
from app.schemas.archive import (
    BindingCreate,
    BindingList,
    BindingOut,
    ClassroomCreate,
    ClassroomList,
    ClassroomOut,
    ClassroomUpdate,
    InviteCodeCreate,
    InviteCodeList,
    InviteCodeOut,
    ParentCreate,
    ParentList,
    ParentOut,
    ParentUpdate,
    StudentCreate,
    StudentList,
    StudentOut,
    StudentUpdate,
    TeacherCreate,
    TeacherList,
    TeacherOut,
    TeacherUpdate,
)

router = APIRouter(tags=["archive"])

Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


async def _count(db: AsyncSession, stmt) -> int:
    subquery = stmt.order_by(None).subquery()
    return int(await db.scalar(select(func.count()).select_from(subquery)) or 0)


def _new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def _changes(payload, required: tuple[str, ...]) -> dict:
    """Reject explicit nulls for NOT NULL columns; omission still means no change."""

    data = payload.model_dump(exclude_unset=True)
    for field in required:
        if field in data and data[field] is None:
            raise AppError(422, "VALIDATION_ERROR", f"{field} 不能为空")
    return data


def _invite_status(invite: InviteCode, now: datetime) -> str:
    if invite.used_at is not None:
        return "used"
    if invite.expires_at <= now:
        return "expired"
    return "valid"


# ---------------------------------------------------------------- students


@router.post("/students", response_model=StudentOut, status_code=201, summary="新增学生")
async def create_student(
    payload: StudentCreate, db: TenantDbSession, principal: OfficeUser
) -> StudentOut:
    student = Student(tenant_id=principal.tenant_id, **payload.model_dump())
    db.add(student)
    await db.flush()
    return StudentOut.model_validate(student)


@router.get("/students", response_model=StudentList, summary="学生列表")
async def list_students(
    db: TenantDbSession,
    principal: TeacherOrOffice,
    name: str | None = None,
    grade: str | None = None,
    status: str | None = None,
    page: Page = 1,
    page_size: PageSize = 20,
) -> StudentList:
    stmt = select(Student)
    count_stmt = select(func.count(func.distinct(Student.id))).select_from(Student)
    if principal.role == "teacher":
        joins = (
            ClassEnrollment,
            ClassEnrollment.student_id == Student.id,
            Class,
            Class.id == ClassEnrollment.class_id,
        )
        stmt = stmt.join(joins[0], joins[1]).join(joins[2], joins[3])
        count_stmt = count_stmt.join(joins[0], joins[1]).join(joins[2], joins[3])
        teacher_filter = Class.teacher_id == principal.teacher_id
        stmt = stmt.where(teacher_filter).distinct()
        count_stmt = count_stmt.where(teacher_filter)
    if name:
        stmt = stmt.where(Student.name.ilike(f"%{name}%"))
        count_stmt = count_stmt.where(Student.name.ilike(f"%{name}%"))
    if grade:
        stmt = stmt.where(Student.grade == grade)
        count_stmt = count_stmt.where(Student.grade == grade)
    if status:
        stmt = stmt.where(Student.status == status)
        count_stmt = count_stmt.where(Student.status == status)

    total = int(await db.scalar(count_stmt) or 0)
    rows = await db.scalars(
        stmt.order_by(Student.created_at.desc(), Student.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return StudentList(items=[StudentOut.model_validate(row) for row in rows], total=total)


@router.get("/students/{student_id}", response_model=StudentOut, summary="学生详情")
async def get_student(
    student_id: UUID, db: TenantDbSession, principal: CurrentPrincipal
) -> StudentOut:
    student = await db.get(Student, student_id)
    if student is None:
        raise AppError(404, "STUDENT_NOT_FOUND", "学生不存在")

    if principal.role == "teacher":
        allowed = await db.scalar(
            select(ClassEnrollment.id)
            .join(Class, Class.id == ClassEnrollment.class_id)
            .where(
                ClassEnrollment.student_id == student_id,
                Class.teacher_id == principal.teacher_id,
            )
            .limit(1)
        )
    elif principal.role == "parent":
        allowed = await db.scalar(
            select(ParentStudent.id)
            .join(Parent, Parent.id == ParentStudent.parent_id)
            .where(
                Parent.wechat_user_id == principal.subject_id,
                ParentStudent.student_id == student_id,
            )
            .limit(1)
        )
    elif principal.role in {"admin", "registrar"}:
        allowed = True
    else:
        allowed = False

    if not allowed:
        raise AppError(403, "OUT_OF_SCOPE", "无权查看该学生")
    return StudentOut.model_validate(student)


@router.patch("/students/{student_id}", response_model=StudentOut, summary="更新学生（删=归档）")
async def update_student(
    student_id: UUID,
    payload: StudentUpdate,
    db: TenantDbSession,
    principal: OfficeUser,
) -> StudentOut:
    student = await db.get(Student, student_id)
    if student is None:
        raise AppError(404, "STUDENT_NOT_FOUND", "学生不存在")
    for field, value in _changes(payload, ("name", "status")).items():
        setattr(student, field, value)
    await db.flush()
    return StudentOut.model_validate(student)


# ---------------------------------------------------------------- parents


@router.post("/parents", response_model=ParentOut, status_code=201, summary="新增家长")
async def create_parent(
    payload: ParentCreate, db: TenantDbSession, principal: OfficeUser
) -> ParentOut:
    duplicated = await db.scalar(select(Parent.id).where(Parent.phone == payload.phone).limit(1))
    if duplicated is not None:
        raise AppError(409, "PARENT_DUPLICATED", "同手机号家长已存在")
    if payload.wechat_user_id is not None:
        exists = await db.scalar(
            select(WechatUser.id).where(WechatUser.id == payload.wechat_user_id)
        )
        if exists is None:
            raise AppError(422, "WECHAT_USER_NOT_FOUND", "微信身份不存在")
    parent = Parent(tenant_id=principal.tenant_id, **payload.model_dump())
    db.add(parent)
    await db.flush()
    return ParentOut.model_validate(parent)


@router.get("/parents", response_model=ParentList, summary="家长列表")
async def list_parents(
    db: TenantDbSession,
    principal: OfficeUser,
    phone: str | None = None,
    page: Page = 1,
    page_size: PageSize = 20,
) -> ParentList:
    stmt = select(Parent)
    if phone:
        stmt = stmt.where(Parent.phone.ilike(f"%{phone}%"))
    total = await _count(db, stmt)
    rows = await db.scalars(
        stmt.order_by(Parent.created_at.desc(), Parent.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return ParentList(items=[ParentOut.model_validate(row) for row in rows], total=total)


@router.patch("/parents/{parent_id}", response_model=ParentOut, summary="更新家长")
async def update_parent(
    parent_id: UUID,
    payload: ParentUpdate,
    db: TenantDbSession,
    principal: OfficeUser,
) -> ParentOut:
    parent = await db.get(Parent, parent_id)
    if parent is None:
        raise AppError(404, "PARENT_NOT_FOUND", "家长不存在")
    changes = _changes(payload, ("name", "phone"))
    if "phone" in changes:
        duplicated = await db.scalar(
            select(Parent.id)
            .where(Parent.phone == changes["phone"], Parent.id != parent_id)
            .limit(1)
        )
        if duplicated is not None:
            raise AppError(409, "PARENT_DUPLICATED", "同手机号家长已存在")
    for field, value in changes.items():
        setattr(parent, field, value)
    await db.flush()
    return ParentOut.model_validate(parent)


# ---------------------------------------------------------------- teachers


@router.post("/teachers", response_model=TeacherOut, status_code=201, summary="新增老师")
async def create_teacher(
    payload: TeacherCreate, db: TenantDbSession, principal: OfficeUser
) -> TeacherOut:
    teacher = Teacher(tenant_id=principal.tenant_id, **payload.model_dump())
    db.add(teacher)
    await db.flush()
    return TeacherOut.model_validate(teacher)


@router.get("/teachers/me", response_model=TeacherOut, summary="老师本人档案")
async def my_teacher_profile(db: TenantDbSession, principal: CurrentPrincipal) -> TeacherOut:
    if principal.role != "teacher" or principal.teacher_id is None:
        raise AppError(403, "OUT_OF_SCOPE", "仅老师可查看本人档案")
    teacher = await db.get(Teacher, principal.teacher_id)
    if teacher is None:
        raise AppError(404, "TEACHER_NOT_FOUND", "老师不存在")
    return TeacherOut.model_validate(teacher)


@router.get("/teachers", response_model=TeacherList, summary="老师列表")
async def list_teachers(
    db: TenantDbSession,
    principal: OfficeUser,
    status: str | None = None,
    page: Page = 1,
    page_size: PageSize = 20,
) -> TeacherList:
    stmt = select(Teacher)
    if status:
        stmt = stmt.where(Teacher.status == status)
    total = await _count(db, stmt)
    rows = await db.scalars(
        stmt.order_by(Teacher.created_at.desc(), Teacher.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return TeacherList(items=[TeacherOut.model_validate(row) for row in rows], total=total)


@router.patch("/teachers/{teacher_id}", response_model=TeacherOut, summary="更新老师（离职=停用）")
async def update_teacher(
    teacher_id: UUID,
    payload: TeacherUpdate,
    db: TenantDbSession,
    principal: OfficeUser,
) -> TeacherOut:
    teacher = await db.get(Teacher, teacher_id)
    if teacher is None:
        raise AppError(404, "TEACHER_NOT_FOUND", "老师不存在")
    for field, value in _changes(payload, ("name", "status")).items():
        setattr(teacher, field, value)
    await db.flush()
    return TeacherOut.model_validate(teacher)


# ---------------------------------------------------------------- classrooms


@router.post("/classrooms", response_model=ClassroomOut, status_code=201, summary="新增教室")
async def create_classroom(
    payload: ClassroomCreate, db: TenantDbSession, principal: OfficeUser
) -> ClassroomOut:
    classroom = Classroom(tenant_id=principal.tenant_id, **payload.model_dump())
    db.add(classroom)
    await db.flush()
    return ClassroomOut.model_validate(classroom)


@router.get("/classrooms", response_model=ClassroomList, summary="教室列表")
async def list_classrooms(
    db: TenantDbSession,
    principal: TeacherOrOffice,
    page: Page = 1,
    page_size: PageSize = 20,
) -> ClassroomList:
    stmt = select(Classroom)
    if principal.role == "teacher":
        stmt = (
            stmt.join(Class, Class.classroom_id == Classroom.id)
            .where(Class.teacher_id == principal.teacher_id)
            .distinct()
        )
    total = await _count(db, stmt)
    rows = await db.scalars(
        stmt.order_by(Classroom.created_at.desc(), Classroom.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return ClassroomList(items=[ClassroomOut.model_validate(row) for row in rows], total=total)


@router.patch("/classrooms/{classroom_id}", response_model=ClassroomOut, summary="更新教室")
async def update_classroom(
    classroom_id: UUID,
    payload: ClassroomUpdate,
    db: TenantDbSession,
    principal: OfficeUser,
) -> ClassroomOut:
    classroom = await db.get(Classroom, classroom_id)
    if classroom is None:
        raise AppError(404, "CLASSROOM_NOT_FOUND", "教室不存在")
    for field, value in _changes(payload, ("name", "capacity")).items():
        setattr(classroom, field, value)
    await db.flush()
    return ClassroomOut.model_validate(classroom)


# ---------------------------------------------------------------- bindings


@router.post("/bindings", response_model=BindingOut, status_code=201, summary="教务直绑")
async def create_binding(
    payload: BindingCreate, db: TenantDbSession, principal: OfficeUser
) -> BindingOut:
    parent = await db.get(Parent, payload.parent_id)
    if parent is None:
        raise AppError(404, "PARENT_NOT_FOUND", "家长不存在")
    student = await db.get(Student, payload.student_id)
    if student is None:
        raise AppError(404, "STUDENT_NOT_FOUND", "学生不存在")
    if student.status == "archived":
        raise AppError(422, "STUDENT_ARCHIVED", "归档学生不可绑定")
    duplicated = await db.scalar(
        select(ParentStudent.id).where(
            ParentStudent.parent_id == payload.parent_id,
            ParentStudent.student_id == payload.student_id,
        )
    )
    if duplicated is not None:
        raise AppError(409, "BINDING_EXISTS", "重复绑定")

    binding = ParentStudent(tenant_id=principal.tenant_id, **payload.model_dump())
    db.add(binding)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise AppError(409, "BINDING_EXISTS", "重复绑定") from exc
    return BindingOut.model_validate(binding)


@router.get("/bindings", response_model=BindingList, summary="绑定关系列表")
async def list_bindings(
    db: TenantDbSession,
    principal: OfficeUser,
    student_id: UUID | None = None,
    parent_id: UUID | None = None,
    page: Page = 1,
    page_size: PageSize = 20,
) -> BindingList:
    stmt = select(ParentStudent)
    if student_id:
        stmt = stmt.where(ParentStudent.student_id == student_id)
    if parent_id:
        stmt = stmt.where(ParentStudent.parent_id == parent_id)
    total = await _count(db, stmt)
    rows = await db.scalars(
        stmt.order_by(ParentStudent.created_at.desc(), ParentStudent.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return BindingList(items=[BindingOut.model_validate(row) for row in rows], total=total)


@router.delete("/bindings/{binding_id}", status_code=204, summary="解绑")
async def delete_binding(binding_id: UUID, db: TenantDbSession, principal: OfficeUser) -> Response:
    binding = await db.get(ParentStudent, binding_id)
    if binding is None:
        raise AppError(404, "BINDING_NOT_FOUND", "绑定关系不存在")
    await db.delete(binding)
    await db.flush()
    return Response(status_code=204)


# ---------------------------------------------------------------- invite codes


@router.post("/invite-codes", response_model=InviteCodeOut, status_code=201, summary="生成邀请码")
async def create_invite_code(
    payload: InviteCodeCreate, db: TenantDbSession, principal: OfficeUser
) -> InviteCodeOut:
    if not 1 <= payload.expires_in_hours <= 720:
        raise AppError(422, "EXPIRES_INVALID", "有效期需在 1-720 小时之间")
    student = await db.get(Student, payload.student_id)
    if student is None:
        raise AppError(404, "STUDENT_NOT_FOUND", "学生不存在")
    if student.status == "archived":
        raise AppError(422, "STUDENT_ARCHIVED", "归档学生不可生成邀请码")

    invite = InviteCode(
        tenant_id=principal.tenant_id,
        code=_new_code(),
        student_id=student.id,
        expires_at=datetime.now(UTC) + timedelta(hours=payload.expires_in_hours),
        created_by=principal.subject_id,  # type: ignore[arg-type]
    )
    db.add(invite)
    await db.flush()
    return InviteCodeOut(
        code=invite.code,
        student_id=invite.student_id,
        status="valid",
        expires_at=invite.expires_at,
    )


@router.get(
    "/students/{student_id}/invite-codes",
    response_model=InviteCodeList,
    summary="学生邀请码记录",
)
async def list_invite_codes(
    student_id: UUID, db: TenantDbSession, principal: OfficeUser
) -> InviteCodeList:
    student = await db.get(Student, student_id)
    if student is None:
        raise AppError(404, "STUDENT_NOT_FOUND", "学生不存在")
    now = datetime.now(UTC)
    rows = await db.scalars(
        select(InviteCode)
        .where(InviteCode.student_id == student_id)
        .order_by(InviteCode.created_at.desc())
    )
    return InviteCodeList(
        items=[
            InviteCodeOut(
                code=invite.code,
                student_id=invite.student_id,
                status=_invite_status(invite, now),
                expires_at=invite.expires_at,
                used_by=invite.used_by_wechat_user_id,
            )
            for invite in rows
        ]
    )


# ---------------------------------------------------------------- classes (read)
# GET /classes now lives in app/api/routes/course.py (2.2), where the class
# payload carries course / teacher / classroom names and the live roster size.
