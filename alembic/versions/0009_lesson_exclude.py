"""lesson overlap exclusion constraints (3.3)

Revision ID: 0009_lesson_exclude
Revises: 0008_tenant_holiday
Create Date: 2026-10-30 05:40:00.000000+08:00

3.2 generated lessons without any conflict detection, so existing rows may
already overlap. Building the EXCLUDE constraints straight away would fail on
such rows, so we first cancel the stale overlaps (keeping the lowest ``id`` per
clashing pair, per the 022 instruction) and only then add the constraints.

Overlaps are *cancelled*, not physically deleted: a stale lesson can already be
referenced by ``lesson_ledger`` (a signed-in lesson that consumed a package), so
a hard DELETE would trip the foreign key. ``cancelled`` both preserves the audit
trail and is exactly the status the EXCLUDE predicate already ignores.

The ``btree_gist`` extension backs the ``uuid WITH =`` operators inside a GiST
index; ``tstzrange &&`` is built-in. No existing table is altered beyond the
two constraints on ``lesson``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_lesson_exclude"
down_revision: str | None = "0008_tenant_holiday"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _cancel_overlaps(connection, column: str) -> int:
    """Cancel overlapping lessons on one dimension, keeping the lowest id.

    Loops until no pair overlaps, because one pass can leave a chain of
    overlaps behind (A-B-C where A and C no longer overlap but B and C do).
    """

    total = 0
    while True:
        result = connection.execute(
            sa.text(
                f"""
                UPDATE lesson a
                SET status = 'cancelled'
                FROM lesson b
                WHERE a.id > b.id
                  AND a.{column} = b.{column}
                  AND a.status <> 'cancelled'
                  AND b.status <> 'cancelled'
                  AND a.start_at < b.end_at
                  AND a.end_at > b.start_at
                """
            )
        )
        removed = result.rowcount
        total += removed
        if removed == 0:
            return total


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS btree_gist"))

    teacher_cancelled = _cancel_overlaps(bind, "teacher_id")
    classroom_cancelled = _cancel_overlaps(bind, "classroom_id")
    print(
        "0009_lesson_exclude: cancelled "
        f"{teacher_cancelled} teacher-overlap and {classroom_cancelled} classroom-overlap rows"
    )

    bind.execute(
        sa.text(
            "ALTER TABLE lesson ADD CONSTRAINT lesson_teacher_time_no_overlap "
            "EXCLUDE USING gist (teacher_id WITH =, tstzrange(start_at, end_at) WITH &&) "
            "WHERE (status <> 'cancelled')"
        )
    )
    bind.execute(
        sa.text(
            "ALTER TABLE lesson ADD CONSTRAINT lesson_classroom_time_no_overlap "
            "EXCLUDE USING gist (classroom_id WITH =, tstzrange(start_at, end_at) WITH &&) "
            "WHERE (status <> 'cancelled')"
        )
    )


def downgrade() -> None:
    op.execute("ALTER TABLE lesson DROP CONSTRAINT IF EXISTS lesson_classroom_time_no_overlap")
    op.execute("ALTER TABLE lesson DROP CONSTRAINT IF EXISTS lesson_teacher_time_no_overlap")
