"""invite codes for parent self-binding

Revision ID: 0005_invite_codes
Revises: 0004_provisioning_grants
Create Date: 2026-10-30 03:45:00.000000+08:00

0.5 requires one-time invite codes but the 0.4 dictionary has no table for them.
This adds the 25th table, tenant RLS, and a SECURITY DEFINER lookup so a parent
without a tenant context can locate a code before binding.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_invite_codes"
down_revision: str | None = "0004_provisioning_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"


def upgrade() -> None:
    op.create_table(
        "invite_code",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("student_id", sa.Uuid(), sa.ForeignKey("student.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column(
            "used_by_wechat_user_id",
            sa.Uuid(),
            sa.ForeignKey("wechat_user.id"),
            nullable=True,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("code", name="uq_invite_code_code"),
    )
    op.create_index(
        "ix_invite_code_tenant_id_student_id", "invite_code", ["tenant_id", "student_id"]
    )

    predicate = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    op.execute("ALTER TABLE invite_code ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE invite_code FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_select ON invite_code FOR SELECT "
        f"USING (tenant_id = {predicate})"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_modify ON invite_code FOR ALL "
        f"USING (tenant_id = {predicate}) WITH CHECK (tenant_id = {predicate})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE invite_code TO {APP_ROLE}")

    op.execute(
        """
        CREATE FUNCTION auth_invite_code(p_code text)
        RETURNS TABLE (
            id uuid,
            tenant_id uuid,
            student_id uuid,
            expires_at timestamptz,
            used_at timestamptz
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT i.id, i.tenant_id, i.student_id, i.expires_at, i.used_at
            FROM invite_code AS i
            WHERE i.code = p_code
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION auth_invite_code(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION auth_invite_code(text) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth_invite_code(text)")
    op.drop_index("ix_invite_code_tenant_id_student_id", table_name="invite_code")
    op.drop_table("invite_code")
