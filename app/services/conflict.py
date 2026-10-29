"""Scheduling conflict detection (3.3).

Three dimensions, aggregated in the order the API spec lists them: teacher →
classroom → student. Boundary-touching slots do NOT overlap — a lesson ending
at 19:30 and another starting at 19:30 are both fine. Cancelled lessons never
occupy a slot.

``find_conflicts`` reads only; it never raises. Callers decide what a hit means
(the batch generator skips and counts, a single insert raises 409). The
student dimension is handled purely in SQL through ``class_enrollment`` and is
*not* backed by an EXCLUDE constraint (many-to-many cannot be expressed there);
it relies on this pre-check plus the concurrency test.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import ClassEnrollment
from app.models.schedule import Lesson
from app.services.ledger import BUSINESS_TZ


@dataclass(frozen=True, slots=True)
class Conflict:
    """A single clashing lesson, with the dimension it clashed on."""

    kind: str  # "teacher" | "classroom" | "student"
    lesson_id: UUID
    class_id: UUID
    start_at: datetime
    end_at: datetime

    def to_dict(self) -> dict:
        return {
            "type": self.kind,
            "lesson_id": str(self.lesson_id),
            "class_id": str(self.class_id),
            "start_at": self.start_at.astimezone(BUSINESS_TZ).isoformat(),
            "end_at": self.end_at.astimezone(BUSINESS_TZ).isoformat(),
        }


def _overlaps(start_at: datetime, end_at: datetime):
    # start < other.end AND end > other.start；边界相接（==）不算重叠。
    return (Lesson.start_at < end_at) & (Lesson.end_at > start_at)


def _to_conflicts(kind: str, lessons) -> list[Conflict]:
    return [
        Conflict(kind, lesson.id, lesson.class_id, lesson.start_at, lesson.end_at)
        for lesson in lessons
    ]


async def find_conflicts(
    db: AsyncSession,
    *,
    classroom_id: UUID,
    teacher_id: UUID,
    class_id: UUID,
    start_at: datetime,
    end_at: datetime,
) -> list[Conflict]:
    """Return every lesson that would clash with the proposed slot.

    Runs inside the caller's tenant context, so RLS already scopes every query
    to the current tenant — foreign rows are invisible, exactly like 3.1/3.2.
    """

    conflicts: list[Conflict] = []

    teacher_clashes = await db.scalars(
        select(Lesson).where(
            Lesson.teacher_id == teacher_id,
            Lesson.status != "cancelled",
            _overlaps(start_at, end_at),
        )
    )
    conflicts.extend(_to_conflicts("teacher", teacher_clashes))

    classroom_clashes = await db.scalars(
        select(Lesson).where(
            Lesson.classroom_id == classroom_id,
            Lesson.status != "cancelled",
            _overlaps(start_at, end_at),
        )
    )
    conflicts.extend(_to_conflicts("classroom", classroom_clashes))

    # 学员维度：本班所有学生，在「其他班」里与本次时段重叠的课次。
    classmates = select(ClassEnrollment.student_id).where(ClassEnrollment.class_id == class_id)
    student_clashes = await db.scalars(
        select(Lesson).where(
            Lesson.status != "cancelled",
            Lesson.class_id != class_id,
            _overlaps(start_at, end_at),
            Lesson.class_id.in_(
                select(ClassEnrollment.class_id).where(ClassEnrollment.student_id.in_(classmates))
            ),
        )
    )
    conflicts.extend(_to_conflicts("student", student_clashes))

    return conflicts
