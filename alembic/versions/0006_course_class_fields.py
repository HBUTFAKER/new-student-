"""course and class fields for the 2.1/2.2 module

Revision ID: 0006_course_class_fields
Revises: 0005_invite_codes
Create Date: 2026-10-30 03:40:00.000000+08:00

0.4 defined the minimum columns; 2.1/2.2 need the course template fields
(duration / remark / status) and the class term window and lifecycle status so
enrolment can reject classes that already finished.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_course_class_fields"
down_revision: str | None = "0005_invite_codes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("course", sa.Column("duration_min", sa.Integer(), nullable=True))
    op.add_column("course", sa.Column("remark", sa.String(length=255), nullable=True))
    op.add_column(
        "course",
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
    )

    op.add_column("class", sa.Column("term_start", sa.Date(), nullable=True))
    op.add_column("class", sa.Column("term_end", sa.Date(), nullable=True))
    op.add_column(
        "class",
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
    )
    op.create_index("ix_class_tenant_id_status", "class", ["tenant_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_class_tenant_id_status", table_name="class")
    op.drop_column("class", "status")
    op.drop_column("class", "term_end")
    op.drop_column("class", "term_start")
    op.drop_column("course", "status")
    op.drop_column("course", "remark")
    op.drop_column("course", "duration_min")
