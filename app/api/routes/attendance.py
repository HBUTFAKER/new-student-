"""Check-in endpoints (3.5, US-102/208).

写（点名）与读（结果）都收口 ``TeacherOrOffice``：教务/管理员可操作任意课次，
老师只能操作自己任教的班级课次（service 层按 ``Class.teacher_id`` 收窄，
否则 403 ``OUT_OF_SCOPE``）。家长一律 403。
"""

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import TenantDbSession
from app.api.permissions import TeacherOrOffice
from app.core.auth import Principal
from app.schemas.attendance import (
    AttendanceList,
    AttendanceMarkIn,
    AttendanceMarkOut,
    AttendanceResultOut,
    AttendanceRowOut,
)
from app.services.attendance import CheckInRequest, check_in, list_attendance

router = APIRouter(tags=["attendance"])


def _teacher_scope(principal: Principal) -> UUID | None:
    """老师的任教身份；教务/管理员返回 None（不收紧）。"""

    return principal.teacher_id if principal.role == "teacher" else None


@router.post(
    "/lessons/{lesson_id}/attendance",
    response_model=AttendanceMarkOut,
    summary="课堂点名签到（同事务扣课，幂等）",
)
async def mark_attendance(
    lesson_id: UUID,
    payload: AttendanceMarkIn,
    db: TenantDbSession,
    principal: TeacherOrOffice,
) -> AttendanceMarkOut:
    results = await check_in(
        db,
        tenant_id=principal.tenant_id,
        lesson_id=lesson_id,
        records=[
            CheckInRequest(student_id=record.student_id, status=record.status)
            for record in payload.records
        ],
        operator_id=principal.subject_id,
        teacher_id=_teacher_scope(principal),
    )
    return AttendanceMarkOut(
        lesson_id=lesson_id,
        results=[
            AttendanceResultOut(
                student_id=result.student_id,
                status=result.status,
                deduct_lessons=result.deduct_lessons,
                deduplicated=result.deduplicated,
                reason=result.reason,
            )
            for result in results
        ],
    )


@router.get(
    "/lessons/{lesson_id}/attendance",
    response_model=AttendanceList,
    summary="课堂点名结果",
)
async def get_attendance(
    lesson_id: UUID, db: TenantDbSession, principal: TeacherOrOffice
) -> AttendanceList:
    rows = await list_attendance(db, lesson_id=lesson_id, teacher_id=_teacher_scope(principal))
    return AttendanceList(
        items=[
            AttendanceRowOut(
                student_id=row.student_id,
                status=row.status,
                deduct_lessons=row.deduct_lessons,
                operator_id=row.operator_id,
            )
            for row in rows
        ],
        total=len(rows),
    )
