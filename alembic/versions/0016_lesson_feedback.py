"""lesson feedback table (048)

Revision ID: 0016_lesson_feedback
Revises: 0015_org_config_scheduler
Create Date: 2026-11-03 04:00:00.000000+08:00

048 课堂反馈（一期收官步）：老师课后给家长写「今天讲了啥、孩子表现如何」。
一期纯文字：不建独立编号 / 评分 / 图片字段（打分、图片二期）。

唯一约束 ``(lesson_id, student_id)`` = 一个学生一节课最多一条反馈；批量 upsert
（``INSERT ... ON CONFLICT DO UPDATE``）靠它幂等，重复 PUT 覆盖更新不炸 500。

``updated_at`` 是第 4 张带该列的表（前三张 org_config / subscribe_auth / notification），
记录最后一次覆盖更新的时间。

新增 1 张表：总数 28 → 29（业务层 21 → 22），``test_models`` / ``test_migrations``
的硬编码同步改。业务层 +1 表必须进 ``RLS_TABLES``（``app/models/__init__.py``）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_lesson_feedback"
down_revision: str | None = "0015_org_config_scheduler"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"


def upgrade() -> None:
    op.create_table(
        "lesson_feedback",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("lesson_id", sa.Uuid(), sa.ForeignKey("lesson.id"), nullable=False),
        sa.Column("student_id", sa.Uuid(), sa.ForeignKey("student.id"), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), sa.ForeignKey("teacher.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
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
        sa.UniqueConstraint("lesson_id", "student_id", name="uq_lesson_feedback_lesson_student"),
    )

    op.execute(
        "CREATE INDEX ix_lesson_feedback_tenant_id_lesson_id "
        "ON lesson_feedback (tenant_id, lesson_id)"
    )
    op.execute(
        "CREATE INDEX ix_lesson_feedback_tenant_id_student_id_created_at "
        "ON lesson_feedback (tenant_id, student_id, created_at DESC)"
    )

    predicate = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    op.execute("ALTER TABLE lesson_feedback ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE lesson_feedback FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_select ON lesson_feedback FOR SELECT "
        f"USING (tenant_id = {predicate})"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_modify ON lesson_feedback FOR ALL "
        f"USING (tenant_id = {predicate}) WITH CHECK (tenant_id = {predicate})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE lesson_feedback TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("lesson_feedback")
