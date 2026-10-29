"""create the non-superuser application role and enforce tenant RLS

Revision ID: 0002_rls_app_role
Revises: 0001_initial_schema
Create Date: 2026-10-30 01:40:00.000000+08:00

0001 created the policies; this revision makes them effective by introducing a
LOGIN role that is neither a superuser nor the table owner. Runtime DATABASE_URL
must use app_user, while Alembic keeps using MIGRATION_DATABASE_URL.
"""

import os
from collections.abc import Sequence

from alembic import op

revision: str = "0002_rls_app_role"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"
# 17 business tables plus the tenant-scoped subscribe_auth table.
TENANT_TABLES = (
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
WECHAT_TABLES = ("wechat_user", "wechat_openid")


def upgrade() -> None:
    """Create a least-privilege runtime role and grant table DML."""
    password = os.getenv("APP_DB_PASSWORD", "app_user_dev_password")
    password_literal = "'" + password.replace("'", "''") + "'"
    # The role name is a fixed internal constant; the password is escaped as a
    # SQL literal, never treated as an identifier.
    op.execute(
        f"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOBYPASSRLS PASSWORD {password_literal};
    ELSE
        ALTER ROLE {APP_ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOBYPASSRLS PASSWORD {password_literal};
    END IF;
END
$$
"""
    )
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")

    # Platform tables (plan/tenant/tenant_subscription/usage_meter) are not
    # granted: tenant application traffic must not read platform-wide rows.
    for table in (*TENANT_TABLES, *WECHAT_TABLES):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO {APP_ROLE}")

    # Keep grants valid when future migrations create tables as the migration
    # owner. session_user is resolved inside the DO block, not interpolated.
    op.execute(
        f"""
DO $$
DECLARE
    migration_owner text := session_user;
BEGIN
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}',
        migration_owner
    );
END
$$
"""
    )

    # Idempotent enforcement of the 0.3 decision. Replace 0001's policies with
    # NULLIF: pool reset (RESET ALL) can expose an unset custom GUC as '', and
    # casting that empty string to uuid would raise instead of returning empty.
    tenant_predicate = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_select ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_modify ON {table}")
        op.execute(
            f"CREATE POLICY tenant_isolation_select ON {table} FOR SELECT "
            f"USING (tenant_id = {tenant_predicate})"
        )
        op.execute(
            f"CREATE POLICY tenant_isolation_modify ON {table} FOR ALL "
            f"USING (tenant_id = {tenant_predicate}) "
            f"WITH CHECK (tenant_id = {tenant_predicate})"
        )


def downgrade() -> None:
    """Keep the login role: it may be shared by other databases/deployments."""

    # Grants disappear together with their tables in 0001's downgrade.
