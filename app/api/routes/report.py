"""Report endpoints (4.4): attendance rate, lesson consumption, teacher hours.

全部只读聚合，权限收口 ``OfficeUser``（教务/管理员）。老师不开放报表——4.1 的
菜单里老师本就没有报表页，这里保持一致；家长 403。

窗口参数 ``from``/``to`` 必填且必须带时区，日界按 UTC+8（见 services/report.py）。
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import TenantDbSession
from app.api.permissions import OfficeUser
from app.schemas.report import (
    AttendanceSummaryOut,
    ClassAttendanceRow,
    ClassConsumptionRow,
    ConsumptionOut,
    StudentAttendanceRow,
    StudentConsumptionRow,
    TeacherHoursOut,
    TeacherHoursRow,
)
from app.services.report import (
    attendance_summary,
    consumption,
    resolve_window,
    teacher_hours,
)

router = APIRouter(tags=["reports"])

FromQuery = Annotated[datetime, Query(alias="from", description="窗口起点，含，ISO 8601 带时区")]
ToQuery = Annotated[datetime, Query(alias="to", description="窗口终点，不含，ISO 8601 带时区")]


@router.get(
    "/reports/attendance-summary",
    response_model=AttendanceSummaryOut,
    summary="出勤率（按班级聚合，可选按学生展开）",
)
async def attendance_summary_endpoint(
    db: TenantDbSession,
    principal: OfficeUser,
    date_from: FromQuery,
    date_to: ToQuery,
    class_id: UUID | None = None,
    detail: bool = False,
) -> AttendanceSummaryOut:
    start, end = resolve_window(date_from, date_to)
    result = await attendance_summary(db, start=start, end=end, class_id=class_id, detail=detail)

    notes: list[str] = []
    if result.makeup_rows:
        notes.append(f"有 {result.makeup_rows} 条 makeup 预登记考勤，尚未转正，已按出勤计入")

    return AttendanceSummaryOut(
        from_=start,
        to=end,
        items=[
            ClassAttendanceRow(
                class_id=row.class_id,
                class_name=row.class_name,
                lesson_count=row.lesson_count,
                present=row.present,
                leave=row.leave,
                absent=row.absent,
                attendance_rate=row.attendance_rate,
            )
            for row in result.items
        ],
        total=len(result.items),
        student_rows=[
            StudentAttendanceRow(
                student_id=row.student_id,
                student_name=row.student_name,
                present=row.present,
                leave=row.leave,
                absent=row.absent,
                attendance_rate=row.attendance_rate,
            )
            for row in result.student_rows
        ],
        notes=notes,
    )


@router.get(
    "/reports/consumption",
    response_model=ConsumptionOut,
    summary="课耗（按学生聚合，附各班分布）",
)
async def consumption_endpoint(
    db: TenantDbSession,
    principal: OfficeUser,
    date_from: FromQuery,
    date_to: ToQuery,
) -> ConsumptionOut:
    start, end = resolve_window(date_from, date_to)
    result = await consumption(db, start=start, end=end)
    return ConsumptionOut(
        from_=start,
        to=end,
        items=[
            StudentConsumptionRow(
                student_id=row.student_id,
                student_name=row.student_name,
                total_consumed=row.total_consumed,
                lesson_count=row.lesson_count,
            )
            for row in result.items
        ],
        total=len(result.items),
        by_class=[
            ClassConsumptionRow(
                student_id=row.student_id,
                class_id=row.class_id,
                class_name=row.class_name,
                consumed=row.consumed,
                lesson_count=row.lesson_count,
            )
            for row in result.by_class
        ],
    )


@router.get(
    "/reports/teacher-hours",
    response_model=TeacherHoursOut,
    summary="老师课时数（按老师聚合）",
)
async def teacher_hours_endpoint(
    db: TenantDbSession,
    principal: OfficeUser,
    date_from: FromQuery,
    date_to: ToQuery,
) -> TeacherHoursOut:
    start, end = resolve_window(date_from, date_to)
    rows = await teacher_hours(db, start=start, end=end)
    return TeacherHoursOut(
        from_=start,
        to=end,
        items=[
            TeacherHoursRow(
                teacher_id=row.teacher_id,
                teacher_name=row.teacher_name,
                lesson_count=row.lesson_count,
                completed_count=row.completed_count,
                total_minutes=row.total_minutes,
            )
            for row in rows
        ],
        total=len(rows),
    )
