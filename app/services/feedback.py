"""Lesson feedback (048): teacher writes post-class notes, parents read them.

红线：本模块纯读写反馈，不碰通知引擎、不碰扣课 ``ledger.py``、不碰写闸门、不碰
限额。反馈**不发任何通知**（不触发消息推送、不进通知引擎、不占消息额度）。

权限口径：
* 写 / 管理端查：任课老师本人 + 教务 + admin（``TeacherOrOffice``）；
* 老师只能写自己任教课次的反馈，别人的课次 404（沿用跨租户 / 跨班级 404 口径，
  与签到 403 ``OUT_OF_SCOPE`` 不同 —— 048 拍板）。
* ``teacher_id`` 恒等于课次任课老师 ``lesson.teacher_id``：老师本人写即操作者，
  教务 / admin 代写也归到任课老师名下，一期不单独记代写人。
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.archive import Student, Teacher
from app.models.course import Class, Course
from app.models.feedback import LessonFeedback
from app.models.schedule import Lesson
from app.services.ledger import BUSINESS_TZ


@dataclass(frozen=True, slots=True)
class FeedbackWrite:
    student_id: UUID
    content: str


async def _lesson_for_feedback(
    db: AsyncSession, lesson_id: UUID, teacher_id: UUID | None
) -> Lesson:
    """课次必须存在；老师只允许写自己任教课次（否则 404）。"""

    lesson = await db.get(Lesson, lesson_id)
    if lesson is None:
        raise AppError(404, "LESSON_NOT_FOUND", "课次不存在")
    if teacher_id is not None and lesson.teacher_id != teacher_id:
        # 老师写别人任教的课次 → 404（048 口径，与签到 403 不同）。
        raise AppError(404, "LESSON_NOT_FOUND", "课次不存在")
    return lesson


async def upsert_feedback(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    lesson_id: UUID,
    operator_teacher_id: UUID | None,
    items: list[FeedbackWrite],
) -> None:
    """批量 upsert：已存在则覆盖更新（最后保存的为准），不存在则插入。

    幂等靠 ``lesson_feedback`` 唯一键 ``(lesson_id, student_id)``：
    ``INSERT ... ON CONFLICT DO UPDATE``，重复 PUT 覆盖更新不炸 500。
    """

    lesson = await _lesson_for_feedback(db, lesson_id, operator_teacher_id)

    student_ids = {item.student_id for item in items}
    if student_ids:
        # RLS 让别家学生查不到 → 404，顺带挡掉跨租户写入。
        known = set((await db.scalars(select(Student.id).where(Student.id.in_(student_ids)))).all())
        if known != student_ids:
            raise AppError(404, "STUDENT_NOT_FOUND", "学生不存在")

    values = [
        {
            "tenant_id": tenant_id,
            "lesson_id": lesson_id,
            "student_id": item.student_id,
            "teacher_id": lesson.teacher_id,
            "content": item.content,
        }
        for item in items
    ]
    if not values:
        return

    stmt = pg_insert(LessonFeedback).values(values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_lesson_feedback_lesson_student",
        set_={"content": stmt.excluded.content, "updated_at": func.now()},
    )
    await db.execute(stmt)


async def list_lesson_feedback(
    db: AsyncSession, *, lesson_id: UUID, teacher_id: UUID | None
) -> list[tuple[LessonFeedback, str]]:
    """管理端查某课次已写反馈，附带学生姓名。没写的学生不在列表里。"""

    await _lesson_for_feedback(db, lesson_id, teacher_id)
    rows = await db.execute(
        select(LessonFeedback, Student.name)
        .join(Student, Student.id == LessonFeedback.student_id)
        .where(LessonFeedback.lesson_id == lesson_id)
        .order_by(LessonFeedback.created_at, LessonFeedback.id)
    )
    return [(feedback, name) for feedback, name in rows]


async def child_feedback(
    db: AsyncSession, *, student_id: UUID, page: int, page_size: int
) -> tuple[list[dict], int]:
    """家长端：某孩子全部反馈，按 ``created_at`` 倒序分页。

    老师名从 ``LessonFeedback.teacher_id`` join（= 课次任课老师），课程/班级名走
    ``lesson → class → course`` 链路，与课表取数同源。
    """

    base = (
        select(
            LessonFeedback.lesson_id,
            LessonFeedback.content,
            LessonFeedback.created_at,
            Lesson.start_at,
            Class.name,
            Course.name,
            Teacher.name,
        )
        .join(Lesson, Lesson.id == LessonFeedback.lesson_id)
        .join(Class, Class.id == Lesson.class_id)
        .join(Course, Course.id == Class.course_id)
        .join(Teacher, Teacher.id == LessonFeedback.teacher_id)
        .where(LessonFeedback.student_id == student_id)
    )

    counted = select(func.count()).select_from(base.order_by(None).subquery())
    total = int(await db.scalar(counted) or 0)

    rows = await db.execute(
        base.order_by(LessonFeedback.created_at.desc(), LessonFeedback.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        {
            "lesson_id": lesson_id,
            "lesson_date": start_at.astimezone(BUSINESS_TZ),
            "class_name": class_name,
            "course_name": course_name,
            "teacher_name": teacher_name,
            "content": content,
            "created_at": created_at.astimezone(BUSINESS_TZ),
        }
        for lesson_id, content, created_at, start_at, class_name, course_name, teacher_name in rows
    ]
    return items, total
