"""notification centre engine table (6.1)

Revision ID: 0013_notification
Revises: 0012_in_app_message
Create Date: 2026-11-01 23:40:00.000000+08:00

6.1 只交付通知中心的**引擎**：一张通知表、一个状态机、一个幂等入队函数、一个带重试的
worker、一个通道适配器接口。不实现微信通道/短信通道/定时任务（6.2 / 6.4）。

``dedupe_key`` 全局唯一 + 入队 ``ON CONFLICT DO NOTHING`` 是「同一事件不重复发」的第一道
防线；``(status, next_retry_at)`` 索引给 worker 扫描待发队列（跨租户，不从 tenant_id 起，
见 test_models 例外登记）。``updated_at`` 追踪重试时间，是第 3 张带该列的表。

新增 1 张表：总数 27 → 28（业务层 20 → 21），``test_models`` / ``test_migrations``
硬编码同步改。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_notification"
down_revision: str | None = "0012_in_app_message"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"


def upgrade() -> None:
    op.create_table(
        "notification",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "channels",
            postgresql.ARRAY(sa.String(length=16)),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("succeeded_channels", postgresql.ARRAY(sa.String(length=16)), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("dedupe_key", name="uq_notification_dedupe_key"),
    )

    op.execute(
        "CREATE INDEX ix_notification_status_next_retry_at ON notification (status, next_retry_at)"
    )

    predicate = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    op.execute("ALTER TABLE notification ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE notification FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_select ON notification FOR SELECT "
        f"USING (tenant_id = {predicate})"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_modify ON notification FOR ALL "
        f"USING (tenant_id = {predicate}) WITH CHECK (tenant_id = {predicate})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE notification TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("notification")
