"""authentication support: password hash column and controlled lookup functions

Revision ID: 0003_auth_identity
Revises: 0002_rls_app_role
Create Date: 2026-10-30 02:55:00.000000+08:00

Login must resolve an identity before a tenant is known, while every business
table is protected by RLS. Rather than weakening the policies or shipping the
owner credentials in the API, this revision adds two SECURITY DEFINER functions
that return only the columns authentication needs, plus one controlled
cross-tenant binding lookup for parent identity (0.6).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_auth_identity"
down_revision: str | None = "0002_rls_app_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"

FUNCTIONS = (
    "auth_staff_candidates(text)",
    "auth_staff_by_id(uuid)",
    "auth_wechat_bindings(uuid)",
)


def upgrade() -> None:
    """Add storage for argon2 hashes and the minimum auth lookup surface."""

    op.add_column(
        "staff_account",
        sa.Column(
            "password_hash",
            sa.String(length=255),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )

    op.execute(
        """
        CREATE FUNCTION auth_staff_candidates(p_phone text)
        RETURNS TABLE (
            id uuid,
            tenant_id uuid,
            password_hash text,
            role text,
            status text,
            teacher_id uuid
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT s.id, s.tenant_id, s.password_hash::text, s.role, s.status, s.teacher_id
            FROM staff_account AS s
            WHERE s.phone = p_phone
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION auth_staff_by_id(p_staff_id uuid)
        RETURNS TABLE (
            id uuid,
            tenant_id uuid,
            password_hash text,
            role text,
            status text,
            teacher_id uuid
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT s.id, s.tenant_id, s.password_hash::text, s.role, s.status, s.teacher_id
            FROM staff_account AS s
            WHERE s.id = p_staff_id
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION auth_wechat_bindings(p_wechat_user_id uuid)
        RETURNS TABLE (
            parent_id uuid,
            tenant_id uuid,
            student_id uuid,
            student_name text,
            relation text,
            tenant_name text,
            student_status text
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT ps.parent_id,
                   ps.tenant_id,
                   ps.student_id,
                   s.name::text,
                   ps.relation::text,
                   t.name::text,
                   s.status::text
            FROM parent_student AS ps
            JOIN parent AS p
              ON p.id = ps.parent_id AND p.tenant_id = ps.tenant_id
            JOIN student AS s
              ON s.id = ps.student_id AND s.tenant_id = ps.tenant_id
            JOIN tenant AS t
              ON t.id = ps.tenant_id
            WHERE p.wechat_user_id = p_wechat_user_id
            ORDER BY ps.tenant_id, s.name, ps.student_id
        $$
        """
    )

    for signature in FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {APP_ROLE}")


def downgrade() -> None:
    """Drop the auth lookup surface before its backing column."""

    for signature in FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.drop_column("staff_account", "password_hash")
