"""tenant holidays for lesson generation (3.2)

Revision ID: 0008_tenant_holiday
Revises: 0007_purchase_course_ledger
Create Date: 2026-10-30 05:15:00.000000+08:00

0.4 has no holiday concept, but 3.2 must skip institution closures when it
expands a schedule rule into lessons. This adds exactly one table (one row per
closed date, unique per tenant) plus the usual RLS pair and the runtime grant.
No existing table is touched.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_tenant_holiday"
down_revision: str | None = "0007_purchase_course_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"


def upgrade() -> None:
    op.create_table(
        "tenant_holiday",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", "date", name="uq_tenant_holiday_tenant_id_date"),
    )

    predicate = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    op.execute("ALTER TABLE tenant_holiday ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_holiday FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_select ON tenant_holiday FOR SELECT "
        f"USING (tenant_id = {predicate})"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_modify ON tenant_holiday FOR ALL "
        f"USING (tenant_id = {predicate}) WITH CHECK (tenant_id = {predicate})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE tenant_holiday TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("tenant_holiday")
