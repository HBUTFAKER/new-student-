"""initial schema: 24 tables, all indexes and tenant RLS policies

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-10-30 01:10:00.000000+08:00

Implements docs/phase0/04-data-dictionary.md (24 tables) and the isolation
decisions of docs/phase0/03-tenant-isolation.md.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this migration."""
    op.create_table(
        "plan",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("max_students", sa.Integer(), nullable=False),
        sa.Column("max_messages", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "tenant",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "wechat_user",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("unionid", sa.String(length=64)),
        sa.Column("nickname", sa.String(length=64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("unionid", name="uq_wechat_user_unionid"),
    )

    op.create_table(
        "classroom",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("available_slots", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_classroom_tenant_id", "classroom", ["tenant_id"])

    op.create_table(
        "course",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("subject", sa.String(length=64)),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_course_tenant_id", "course", ["tenant_id"])

    op.create_table(
        "course_package",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("total_lessons", sa.Integer(), nullable=False),
        sa.Column("gift_lessons", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("valid_months", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_course_package_tenant_id", "course_package", ["tenant_id"])

    op.create_table(
        "org_config",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "absence_deduct_rule",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
        sa.Column("logo", sa.String(length=255)),
        sa.Column("notify_template", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", name="uq_org_config_tenant_id"),
    )

    op.create_table(
        "parent",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("wechat_user_id", sa.Uuid(), sa.ForeignKey("wechat_user.id")),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_parent_tenant_id_phone", "parent", ["tenant_id", "phone"])

    op.create_table(
        "student",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("grade", sa.String(length=16)),
        sa.Column("school", sa.String(length=128)),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column("contact", sa.String(length=64)),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_student_tenant_id_name", "student", ["tenant_id", "name"])
    op.create_index("ix_student_tenant_id_status", "student", ["tenant_id", "status"])

    op.create_table(
        "subscribe_auth",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("wechat_user_id", sa.Uuid(), sa.ForeignKey("wechat_user.id"), nullable=False),
        sa.Column("template_id", sa.String(length=64), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'authorized'")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "wechat_user_id",
            "template_id",
            name="uq_subscribe_auth_tenant_id_wechat_user_id_template_id",
        ),
    )

    op.create_table(
        "teacher",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("phone", sa.String(length=32)),
        sa.Column("subject", sa.String(length=64)),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_teacher_tenant_id_status", "teacher", ["tenant_id", "status"])

    op.create_table(
        "tenant_subscription",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("plan.id"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_tenant_subscription_tenant_id", "tenant_subscription", ["tenant_id"])

    op.create_table(
        "usage_meter",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("metric", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "uq_usage_meter_tenant_id_metric_period",
        "usage_meter",
        ["tenant_id", "metric", "period"],
        unique=True,
    )

    op.create_table(
        "wechat_openid",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("wechat_user_id", sa.Uuid(), sa.ForeignKey("wechat_user.id"), nullable=False),
        sa.Column("app_id", sa.String(length=64), nullable=False),
        sa.Column("openid", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("app_id", "openid", name="uq_wechat_openid_app_id_openid"),
    )
    op.create_index("ix_wechat_openid_wechat_user_id", "wechat_openid", ["wechat_user_id"])

    op.create_table(
        "class",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("course_id", sa.Uuid(), sa.ForeignKey("course.id"), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), sa.ForeignKey("teacher.id"), nullable=False),
        sa.Column("classroom_id", sa.Uuid(), sa.ForeignKey("classroom.id"), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("capacity", sa.Integer()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_class_tenant_id_classroom_id", "class", ["tenant_id", "classroom_id"])
    op.create_index("ix_class_tenant_id_course_id", "class", ["tenant_id", "course_id"])
    op.create_index("ix_class_tenant_id_teacher_id", "class", ["tenant_id", "teacher_id"])

    op.create_table(
        "parent_student",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("parent.id"), nullable=False),
        sa.Column("student_id", sa.Uuid(), sa.ForeignKey("student.id"), nullable=False),
        sa.Column("relation", sa.String(length=16)),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "parent_id",
            "student_id",
            name="uq_parent_student_tenant_id_parent_id_student_id",
        ),
    )
    op.create_index(
        "ix_parent_student_tenant_id_student_id", "parent_student", ["tenant_id", "student_id"]
    )

    op.create_table(
        "purchase",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("student_id", sa.Uuid(), sa.ForeignKey("student.id"), nullable=False),
        sa.Column("package_id", sa.Uuid(), sa.ForeignKey("course_package.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column(
            "purchased_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.Date()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_purchase_tenant_id_package_id", "purchase", ["tenant_id", "package_id"])
    op.create_index("ix_purchase_tenant_id_student_id", "purchase", ["tenant_id", "student_id"])

    op.create_table(
        "staff_account",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), sa.ForeignKey("teacher.id")),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", "phone", name="uq_staff_account_tenant_id_phone"),
    )
    op.create_index("ix_staff_account_tenant_id_role", "staff_account", ["tenant_id", "role"])

    op.create_table(
        "class_enrollment",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("class_id", sa.Uuid(), sa.ForeignKey("class.id"), nullable=False),
        sa.Column("student_id", sa.Uuid(), sa.ForeignKey("student.id"), nullable=False),
        sa.Column("joined_at", sa.Date(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "class_id",
            "student_id",
            name="uq_class_enrollment_tenant_id_class_id_student_id",
        ),
    )
    op.create_index(
        "ix_class_enrollment_tenant_id_student_id", "class_enrollment", ["tenant_id", "student_id"]
    )

    op.create_table(
        "lesson",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("class_id", sa.Uuid(), sa.ForeignKey("class.id"), nullable=False),
        sa.Column("classroom_id", sa.Uuid(), sa.ForeignKey("classroom.id"), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), sa.ForeignKey("teacher.id"), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'normal'")
        ),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_lesson_tenant_id_class_id_start_at", "lesson", ["tenant_id", "class_id", "start_at"]
    )
    op.create_index(
        "ix_lesson_tenant_id_classroom_id_start_at",
        "lesson",
        ["tenant_id", "classroom_id", "start_at"],
    )
    op.create_index(
        "ix_lesson_tenant_id_teacher_id_start_at", "lesson", ["tenant_id", "teacher_id", "start_at"]
    )

    op.create_table(
        "refund",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("student_id", sa.Uuid(), sa.ForeignKey("student.id"), nullable=False),
        sa.Column("purchase_id", sa.Uuid(), sa.ForeignKey("purchase.id"), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("approver_id", sa.Uuid(), sa.ForeignKey("staff_account.id")),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_refund_tenant_id_status", "refund", ["tenant_id", "status"])
    op.create_index("ix_refund_tenant_id_student_id", "refund", ["tenant_id", "student_id"])

    op.create_table(
        "schedule_rule",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("class_id", sa.Uuid(), sa.ForeignKey("class.id"), nullable=False),
        sa.Column("classroom_id", sa.Uuid(), sa.ForeignKey("classroom.id"), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), sa.ForeignKey("teacher.id"), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_schedule_rule_tenant_id_class_id", "schedule_rule", ["tenant_id", "class_id"]
    )
    op.create_index(
        "ix_schedule_rule_tenant_id_classroom_id_weekday",
        "schedule_rule",
        ["tenant_id", "classroom_id", "weekday"],
    )
    op.create_index(
        "ix_schedule_rule_tenant_id_teacher_id_weekday",
        "schedule_rule",
        ["tenant_id", "teacher_id", "weekday"],
    )

    op.create_table(
        "attendance",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("lesson_id", sa.Uuid(), sa.ForeignKey("lesson.id"), nullable=False),
        sa.Column("student_id", sa.Uuid(), sa.ForeignKey("student.id"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("deduct_lessons", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "lesson_id",
            "student_id",
            name="uq_attendance_tenant_id_lesson_id_student_id",
        ),
    )
    op.create_index("ix_attendance_tenant_id_student_id", "attendance", ["tenant_id", "student_id"])

    op.create_table(
        "lesson_ledger",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("student_id", sa.Uuid(), sa.ForeignKey("student.id"), nullable=False),
        sa.Column("purchase_id", sa.Uuid(), sa.ForeignKey("purchase.id")),
        sa.Column("lesson_id", sa.Uuid(), sa.ForeignKey("lesson.id")),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=255)),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_lesson_ledger_tenant_id_lesson_id", "lesson_ledger", ["tenant_id", "lesson_id"]
    )
    op.create_index(
        "ix_lesson_ledger_tenant_id_student_id_created_at",
        "lesson_ledger",
        ["tenant_id", "student_id", "created_at"],
    )
    op.create_index("ix_lesson_ledger_tenant_id_type", "lesson_ledger", ["tenant_id", "type"])

    # ---- 租户隔离：RLS + FORCE RLS + 两条策略（0.3 决策 2）----
    # 未设置 app.tenant_id 时 current_setting(..., true) 返回 NULL，
    # 比较结果恒为 NULL，策略按空集处理（不会漏出全表）。
    #
    # 注意：RLS 只对非表属主、非 BYPASSRLS、非超级用户的角色生效。当前
    # DATABASE_URL 仍使用建库账号（表属主），因此开发/CI 环境下 RLS 实际被绕过，
    # 租户过滤靠应用层 tenant_id 条件；按 0.3 决策 2 必须在后续步骤引入独立的
    # 应用连接角色（非 owner、无 BYPASSRLS），本迁移已把策略建好备用。
    rls_tables = (
        "student",
        "parent",
        "teacher",
        "classroom",
        "parent_student",
        "course",
        "class",
        "class_enrollment",
        "course_package",
        "purchase",
        "lesson_ledger",
        "schedule_rule",
        "lesson",
        "attendance",
        "staff_account",
        "org_config",
        "refund",
        "subscribe_auth",
    )
    for table in rls_tables:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_select ON {table} FOR SELECT "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )
        op.execute(
            f"CREATE POLICY tenant_isolation_modify ON {table} FOR ALL "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    """Revert this migration."""
    op.drop_index("ix_lesson_ledger_tenant_id_type", table_name="lesson_ledger")
    op.drop_index("ix_lesson_ledger_tenant_id_student_id_created_at", table_name="lesson_ledger")
    op.drop_index("ix_lesson_ledger_tenant_id_lesson_id", table_name="lesson_ledger")
    op.drop_table("lesson_ledger")
    op.drop_index("ix_attendance_tenant_id_student_id", table_name="attendance")
    op.drop_table("attendance")
    op.drop_index("ix_schedule_rule_tenant_id_teacher_id_weekday", table_name="schedule_rule")
    op.drop_index("ix_schedule_rule_tenant_id_classroom_id_weekday", table_name="schedule_rule")
    op.drop_index("ix_schedule_rule_tenant_id_class_id", table_name="schedule_rule")
    op.drop_table("schedule_rule")
    op.drop_index("ix_refund_tenant_id_student_id", table_name="refund")
    op.drop_index("ix_refund_tenant_id_status", table_name="refund")
    op.drop_table("refund")
    op.drop_index("ix_lesson_tenant_id_teacher_id_start_at", table_name="lesson")
    op.drop_index("ix_lesson_tenant_id_classroom_id_start_at", table_name="lesson")
    op.drop_index("ix_lesson_tenant_id_class_id_start_at", table_name="lesson")
    op.drop_table("lesson")
    op.drop_index("ix_class_enrollment_tenant_id_student_id", table_name="class_enrollment")
    op.drop_table("class_enrollment")
    op.drop_index("ix_staff_account_tenant_id_role", table_name="staff_account")
    op.drop_table("staff_account")
    op.drop_index("ix_purchase_tenant_id_student_id", table_name="purchase")
    op.drop_index("ix_purchase_tenant_id_package_id", table_name="purchase")
    op.drop_table("purchase")
    op.drop_index("ix_parent_student_tenant_id_student_id", table_name="parent_student")
    op.drop_table("parent_student")
    op.drop_index("ix_class_tenant_id_teacher_id", table_name="class")
    op.drop_index("ix_class_tenant_id_course_id", table_name="class")
    op.drop_index("ix_class_tenant_id_classroom_id", table_name="class")
    op.drop_table("class")
    op.drop_index("ix_wechat_openid_wechat_user_id", table_name="wechat_openid")
    op.drop_table("wechat_openid")
    op.drop_index("uq_usage_meter_tenant_id_metric_period", table_name="usage_meter")
    op.drop_table("usage_meter")
    op.drop_index("ix_tenant_subscription_tenant_id", table_name="tenant_subscription")
    op.drop_table("tenant_subscription")
    op.drop_index("ix_teacher_tenant_id_status", table_name="teacher")
    op.drop_table("teacher")
    op.drop_table("subscribe_auth")
    op.drop_index("ix_student_tenant_id_status", table_name="student")
    op.drop_index("ix_student_tenant_id_name", table_name="student")
    op.drop_table("student")
    op.drop_index("ix_parent_tenant_id_phone", table_name="parent")
    op.drop_table("parent")
    op.drop_table("org_config")
    op.drop_index("ix_course_package_tenant_id", table_name="course_package")
    op.drop_table("course_package")
    op.drop_index("ix_course_tenant_id", table_name="course")
    op.drop_table("course")
    op.drop_index("ix_classroom_tenant_id", table_name="classroom")
    op.drop_table("classroom")
    op.drop_table("wechat_user")
    op.drop_table("tenant")
    op.drop_table("plan")
