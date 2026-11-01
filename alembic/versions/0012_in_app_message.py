"""in-app messages for parents (5.5)

Revision ID: 0012_in_app_message
Revises: 0011_subscribe_auth_quota
Create Date: 2026-11-01 13:40:00.000000+08:00

5.4 的验收标准要求「未授权订阅的家长仍能在小程序内看到未读提醒」，所以补一条不依赖
微信的兜底通道：一张站内信表。本步只写入「签到扣课」一种事件，但表结构按 6.1 通知
中心的复用需求留好口子。

幂等键 ``(source_type, source_id, parent_id)``：同一个业务事件对同一个家长只有一条
消息 —— 签到重试、并发提交、6.1 的重试发送都靠它兜住。

新增 1 张表：总数 26 → 27（业务层 19 → 20），``test_models`` / ``test_migrations``
的硬编码同步改。``read_at`` 是读侧标记，不是 ``updated_at``，
``test_no_table_has_updated_at`` 不受影响。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_in_app_message"
down_revision: str | None = "0011_subscribe_auth_quota"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"


def upgrade() -> None:
    op.create_table(
        "in_app_message",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("parent.id"), nullable=False),
        sa.Column("student_id", sa.Uuid(), sa.ForeignKey("student.id"), nullable=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("body", sa.String(length=500), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "source_type",
            "source_id",
            "parent_id",
            name="uq_in_app_message_source",
        ),
    )

    # 列表查询：(机构 + 家长) 等值，created_at 倒序；DESC 直接写在索引里，
    # 免得依靠反向扫描（顺便和模型定义保持一致）。
    op.execute(
        "CREATE INDEX ix_in_app_message_tenant_id_parent_id_created_at "
        "ON in_app_message (tenant_id, parent_id, created_at DESC)"
    )

    predicate = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    op.execute("ALTER TABLE in_app_message ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE in_app_message FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_select ON in_app_message FOR SELECT "
        f"USING (tenant_id = {predicate})"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_modify ON in_app_message FOR ALL "
        f"USING (tenant_id = {predicate}) WITH CHECK (tenant_id = {predicate})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE in_app_message TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("in_app_message")
