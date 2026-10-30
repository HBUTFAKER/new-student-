"""subscribe_auth message quota (5.4)

Revision ID: 0011_subscribe_auth_quota
Revises: 0010_org_config_audit
Create Date: 2026-10-31 01:40:00.000000+08:00

订阅授权提前到一期（033 裁决），一次性模板每「接受」一次就攒一次可发送额度，
所以 ``subscribe_auth`` 需要 ``available_count``；``updated_at`` 记录最近一次
授权动作（6.2 发消息时按它判断新鲜度）。

No new table: table count stays 26 (business 19), so ``test_models`` /
``test_migrations`` keep their numbers. ``test_no_table_has_updated_at`` gains a
second documented exception (``org_config`` 是 3.5 的第一个) —— 见
``tests/test_models.py``。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_subscribe_auth_quota"
down_revision: str | None = "0010_org_config_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscribe_auth",
        sa.Column("available_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "subscribe_auth",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscribe_auth", "updated_at")
    op.drop_column("subscribe_auth", "available_count")
