"""purchase.course_id and ledger lookup indexes (2.3 / 2.4)

Revision ID: 0007_purchase_course_ledger
Revises: 0006_course_class_fields
Create Date: 2026-10-30 03:50:00.000000+08:00

2.3 的缴费登记要指明"买的是哪门课"。0.4 的 purchase 只有 package_id，
课时包是不分课程的模板，课程归属只能落在 purchase 上。

同时补两个索引：扣减按 (student_id, expires_at, purchased_at) 选包并加锁，
包余额按 purchase_id 分组求和，没有索引会随流水增长退化成全表扫描。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_purchase_course_ledger"
down_revision: str | None = "0006_course_class_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("purchase", sa.Column("course_id", sa.Uuid(), nullable=False))
    op.create_foreign_key(
        "fk_purchase_course_id_course", "purchase", "course", ["course_id"], ["id"]
    )
    op.create_index(
        "ix_purchase_tenant_id_student_id_expires_at",
        "purchase",
        ["tenant_id", "student_id", "expires_at"],
    )
    op.create_index(
        "ix_lesson_ledger_tenant_id_purchase_id",
        "lesson_ledger",
        ["tenant_id", "purchase_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_lesson_ledger_tenant_id_purchase_id", table_name="lesson_ledger")
    op.drop_index("ix_purchase_tenant_id_student_id_expires_at", table_name="purchase")
    op.drop_constraint("fk_purchase_course_id_course", "purchase", type_="foreignkey")
    op.drop_column("purchase", "course_id")
