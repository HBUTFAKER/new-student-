"""org_config change trail (3.5)

Revision ID: 0010_org_config_audit
Revises: 0009_lesson_exclude
Create Date: 2026-10-30 15:50:00.000000+08:00

US-217 要求「改配置要留痕」。完整审计日志系统（US-304）是后续阶段的事，
本步只做最小实现：``org_config`` 加 ``updated_by`` 与 ``updated_at`` 两列。

No new table, no other table touched — table count stays 26 (business 19), so
``test_models`` / ``test_migrations`` keep their numbers.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_org_config_audit"
down_revision: str | None = "0009_lesson_exclude"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("org_config", sa.Column("updated_by", sa.Uuid(as_uuid=True), nullable=True))
    op.add_column(
        "org_config",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("org_config", "updated_at")
    op.drop_column("org_config", "updated_by")
