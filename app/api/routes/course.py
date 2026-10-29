"""Course and class endpoints (2.1 / 2.2)."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantDbSession
from app.api.permissions import OfficeUser, TeacherOrOffice
from app.core.errors import AppError
from app.models.archive import Classroom, Student, Teacher
from app.models.course import Class, ClassEnrollment, Course
from app.schemas.course import (
    ClassCreate,
    ClassList,
    ClassOut,
    ClassUpdate,
    CourseCreate,
    CourseList,
    CourseOut,
    CourseUpdate,
    EnrollmentCreate,
    EnrollmentList,
    EnrollmentOut,
)

router = APIRouter(tags=["course"])

Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


async def _get_course(db: AsyncSession, course_id: UUID) -> Course:
    course = await db.get(Course, course_id)
    if course is None:
        raise AppError(404, "COURSE_NOT_FOUND", "课程不存在")
    return course


async def _get_class(db: AsyncSession, class_id: UUID) -> Class:
    klass = await db.get(Class, class_id)
    if klass is None:
        raise AppError(404, "CLASS_NOT_FOUND", "班级不存在")
    return klass


async def _get_teacher(db: AsyncSession, teacher_id: UUID) -> Teacher:
    teacher = await db.get(Teacher, teacher_id)
    if teacher is None:
        raise AppError(404, "TEACHER_NOT_FOUND", "老师不存在")
    if teacher.status != "active":
        raise AppError(422, "TEACHER_INACTIVE", "老师已停用，不能带班")
    return teacher


async def _get_classroom(db: AsyncSession, classroom_id: UUID) -> Classroom:
    classroom = await db.get(Classroom, classroom_id)
    if classroom is None:
        raise AppError(404, "CLASSROOM_NOT_FOUND", "教室不存在")
    return classroom


async def _enrolled_count(db: AsyncSession, class_id: UUID) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(ClassEnrollment)
            .where(ClassEnrollment.class_id == class_id)
        )
        or 0
    )


def _ensure_capacity(capacity: int | None, classroom: Classroom) -> None:
    """A class may not seat more students than its room holds."""

    if capacity is not None and capacity > classroom.capacity:
        raise AppError(
            422,
            "CAPACITY_INVALID",
            f"班级容量 {capacity} 超过教室容量 {classroom.capacity}",
        )


def _class_view(row) -> ClassOut:
    klass, course_name, teacher_name, classroom_name, classroom_capacity, enrolled = row
    return ClassOut(
        id=klass.id,
        name=klass.name,
        course_id=klass.course_id,
        course_name=course_name,
        teacher_id=klass.teacher_id,
        teacher_name=teacher_name,
        classroom_id=klass.classroom_id,
        classroom_name=classroom_name,
        classroom_capacity=classroom_capacity,
        capacity=klass.capacity,
        enrolled=int(enrolled or 0),
        term_start=klass.term_start,
        term_end=klass.term_end,
        status=klass.status,
        created_at=klass.created_at,
    )


def _class_select():
    """Class rows joined with the display names and the live roster size."""

    return (
        select(
            Class,
            Course.name,
            Teacher.name,
            Classroom.name,
            Classroom.capacity,
            func.count(ClassEnrollment.id),
        )
        .join(Course, Course.id == Class.course_id)
        .join(Teacher, Teacher.id == Class.teacher_id)
        .join(Classroom, Classroom.id == Class.classroom_id)
        .outerjoin(ClassEnrollment, ClassEnrollment.class_id == Class.id)
        .group_by(Class.id, Course.name, Teacher.name, Classroom.name, Classroom.capacity)
    )


async def _class_detail(db: AsyncSession, class_id: UUID) -> ClassOut:
    row = (await db.execute(_class_select().where(Class.id == class_id))).first()
    if row is None:
        raise AppError(404, "CLASS_NOT_FOUND", "班级不存在")
    return _class_view(row)


def _ensure_visible(klass: Class, principal) -> None:
    """Teachers only ever see the classes they teach; everything else is 404."""

    if principal.role == "teacher" and klass.teacher_id != principal.teacher_id:
        raise AppError(404, "CLASS_NOT_FOUND", "班级不存在")


# ---------------------------------------------------------------- courses


@router.post("/courses", response_model=CourseOut, status_code=201, summary="新增课程")
async def create_course(
    payload: CourseCreate, db: TenantDbSession, principal: OfficeUser
) -> CourseOut:
    course = Course(tenant_id=principal.tenant_id, **payload.model_dump())
    db.add(course)
    await db.flush()
    return CourseOut.model_validate(course)


@router.get("/courses", response_model=CourseList, summary="课程列表")
async def list_courses(
    db: TenantDbSession,
    principal: OfficeUser,
    status: str | None = None,
    keyword: str | None = None,
    page: Page = 1,
    page_size: PageSize = 20,
) -> CourseList:
    stmt = select(Course)
    if status:
        stmt = stmt.where(Course.status == status)
    if keyword:
        stmt = stmt.where(Course.name.ilike(f"%{keyword}%"))

    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = await db.scalars(
        stmt.order_by(Course.created_at.desc(), Course.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return CourseList(items=[CourseOut.model_validate(row) for row in rows], total=total)


@router.get("/courses/{course_id}", response_model=CourseOut, summary="课程详情")
async def get_course(course_id: UUID, db: TenantDbSession, principal: OfficeUser) -> CourseOut:
    return CourseOut.model_validate(await _get_course(db, course_id))


@router.patch("/courses/{course_id}", response_model=CourseOut, summary="更新课程（停用=status）")
async def update_course(
    course_id: UUID, payload: CourseUpdate, db: TenantDbSession, principal: OfficeUser
) -> CourseOut:
    course = await _get_course(db, course_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("name") is None and "name" in data:
        raise AppError(422, "VALIDATION_ERROR", "name 不能为空")
    for field, value in data.items():
        setattr(course, field, value)
    await db.flush()
    return CourseOut.model_validate(course)


@router.delete("/courses/{course_id}", status_code=204, summary="删除课程（被班级引用则拒绝）")
async def delete_course(course_id: UUID, db: TenantDbSession, principal: OfficeUser) -> Response:
    course = await _get_course(db, course_id)
    referenced = await db.scalar(
        select(func.count()).select_from(Class).where(Class.course_id == course_id)
    )
    if int(referenced or 0) > 0:
        raise AppError(409, "COURSE_IN_USE", "课程已被班级引用，只能停用（PATCH status=inactive）")
    await db.delete(course)
    await db.flush()
    return Response(status_code=204)


# ---------------------------------------------------------------- classes


@router.post("/classes", response_model=ClassOut, status_code=201, summary="新增班级")
async def create_class(
    payload: ClassCreate, db: TenantDbSession, principal: OfficeUser
) -> ClassOut:
    await _get_course(db, payload.course_id)
    await _get_teacher(db, payload.teacher_id)
    classroom = await _get_classroom(db, payload.classroom_id)
    if payload.term_end < payload.term_start:
        raise AppError(422, "TERM_INVALID", "学期结束日期不能早于开始日期")
    _ensure_capacity(payload.capacity, classroom)

    klass = Class(
        tenant_id=principal.tenant_id,
        capacity=payload.capacity or classroom.capacity,
        **payload.model_dump(exclude={"capacity"}),
    )
    db.add(klass)
    await db.flush()
    return await _class_detail(db, klass.id)


@router.get("/classes", response_model=ClassList, summary="班级列表（老师自动收窄）")
async def list_classes(
    db: TenantDbSession,
    principal: TeacherOrOffice,
    course_id: UUID | None = None,
    teacher_id: UUID | None = None,
    status: str | None = None,
    page: Page = 1,
    page_size: PageSize = 20,
) -> ClassList:
    stmt = _class_select()
    count_stmt = select(func.count()).select_from(Class)
    if principal.role == "teacher":
        stmt = stmt.where(Class.teacher_id == principal.teacher_id)
        count_stmt = count_stmt.where(Class.teacher_id == principal.teacher_id)
    if course_id:
        stmt = stmt.where(Class.course_id == course_id)
        count_stmt = count_stmt.where(Class.course_id == course_id)
    if teacher_id:
        stmt = stmt.where(Class.teacher_id == teacher_id)
        count_stmt = count_stmt.where(Class.teacher_id == teacher_id)
    if status:
        stmt = stmt.where(Class.status == status)
        count_stmt = count_stmt.where(Class.status == status)

    total = int(await db.scalar(count_stmt) or 0)
    rows = await db.execute(
        stmt.order_by(Class.created_at.desc(), Class.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return ClassList(items=[_class_view(row) for row in rows], total=total)


@router.get("/classes/{class_id}", response_model=ClassOut, summary="班级详情")
async def get_class(class_id: UUID, db: TenantDbSession, principal: TeacherOrOffice) -> ClassOut:
    klass = await _get_class(db, class_id)
    _ensure_visible(klass, principal)
    return await _class_detail(db, class_id)


@router.patch("/classes/{class_id}", response_model=ClassOut, summary="更新班级")
async def update_class(
    class_id: UUID, payload: ClassUpdate, db: TenantDbSession, principal: OfficeUser
) -> ClassOut:
    klass = await _get_class(db, class_id)
    data = payload.model_dump(exclude_unset=True)
    for field in ("teacher_id", "classroom_id", "name", "term_start", "term_end"):
        if field in data and data[field] is None:
            raise AppError(422, "VALIDATION_ERROR", f"{field} 不能为空")

    if "teacher_id" in data:
        await _get_teacher(db, data["teacher_id"])
    classroom = (
        await _get_classroom(db, data["classroom_id"])
        if "classroom_id" in data
        else await _get_classroom(db, klass.classroom_id)
    )

    term_start = data.get("term_start", klass.term_start)
    term_end = data.get("term_end", klass.term_end)
    if term_start and term_end and term_end < term_start:
        raise AppError(422, "TERM_INVALID", "学期结束日期不能早于开始日期")

    capacity = data.get("capacity", klass.capacity)
    _ensure_capacity(capacity, classroom)
    enrolled = await _enrolled_count(db, klass.id)
    if capacity is not None and capacity < enrolled:
        raise AppError(422, "CAPACITY_EXCEEDED", f"已有 {enrolled} 名学员，容量不能低于在读数")

    for field, value in data.items():
        setattr(klass, field, value)
    await db.flush()
    return await _class_detail(db, class_id)


# ---------------------------------------------------------------- roster


@router.post(
    "/classes/{class_id}/students",
    response_model=EnrollmentOut,
    status_code=201,
    summary="招收学员",
)
async def enroll_student(
    class_id: UUID, payload: EnrollmentCreate, db: TenantDbSession, principal: OfficeUser
) -> EnrollmentOut:
    # Lock the class row so two concurrent enrolments cannot both pass the
    # capacity check and overshoot the room.
    klass = await db.get(Class, class_id, with_for_update=True)
    if klass is None:
        raise AppError(404, "CLASS_NOT_FOUND", "班级不存在")
    if klass.status == "ended" or (klass.term_end and klass.term_end < date.today()):
        raise AppError(409, "CLASS_ENDED", "班级已结课，不能再招收学员")

    student = await db.get(Student, payload.student_id)
    if student is None:
        raise AppError(404, "STUDENT_NOT_FOUND", "学生不存在")
    if student.status != "active":
        raise AppError(422, "STUDENT_STATUS_INVALID", "学生不在读，不能入班")

    duplicated = await db.scalar(
        select(ClassEnrollment.id).where(
            ClassEnrollment.class_id == class_id,
            ClassEnrollment.student_id == student.id,
        )
    )
    if duplicated is not None:
        raise AppError(409, "ALREADY_ENROLLED", "该学生已在本班")

    enrolled = await _enrolled_count(db, class_id)
    if klass.capacity is not None and enrolled >= klass.capacity:
        raise AppError(409, "CLASS_FULL", f"班级已满（容量 {klass.capacity}）")

    enrollment = ClassEnrollment(
        tenant_id=principal.tenant_id,
        class_id=class_id,
        student_id=student.id,
        joined_at=date.today(),
    )
    db.add(enrollment)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise AppError(409, "ALREADY_ENROLLED", "该学生已在本班") from exc

    return EnrollmentOut(
        id=enrollment.id,
        student_id=student.id,
        student_name=student.name,
        student_status=student.status,
        joined_at=enrollment.joined_at,
    )


@router.get(
    "/classes/{class_id}/students",
    response_model=EnrollmentList,
    summary="班级学员名单（老师仅任教班级）",
)
async def list_class_students(
    class_id: UUID, db: TenantDbSession, principal: TeacherOrOffice
) -> EnrollmentList:
    klass = await _get_class(db, class_id)
    _ensure_visible(klass, principal)

    rows = await db.execute(
        select(ClassEnrollment, Student)
        .join(Student, Student.id == ClassEnrollment.student_id)
        .where(ClassEnrollment.class_id == class_id)
        .order_by(ClassEnrollment.joined_at, Student.name, ClassEnrollment.id)
    )
    items = [
        EnrollmentOut(
            id=enrollment.id,
            student_id=student.id,
            student_name=student.name,
            student_status=student.status,
            joined_at=enrollment.joined_at,
        )
        for enrollment, student in rows
    ]
    return EnrollmentList(items=items, total=len(items))


@router.delete(
    "/classes/{class_id}/students/{student_id}",
    status_code=204,
    summary="学员退出班级",
)
async def remove_student(
    class_id: UUID,
    student_id: UUID,
    db: TenantDbSession,
    principal: OfficeUser,
) -> Response:
    enrollment = await db.scalar(
        select(ClassEnrollment).where(
            ClassEnrollment.class_id == class_id,
            ClassEnrollment.student_id == student_id,
        )
    )
    if enrollment is None:
        raise AppError(404, "ENROLLMENT_NOT_FOUND", "该学生不在本班")
    await db.delete(enrollment)
    await db.flush()
    return Response(status_code=204)
