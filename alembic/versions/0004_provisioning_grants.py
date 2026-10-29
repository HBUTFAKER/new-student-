"""platform provisioning grants + tenant name lookup index

Revision ID: 0004_provisioning_grants
Revises: 0003_auth_identity
Create Date: 2026-10-30 03:20:00.000000+08:00

1.5 provisions tenants from the API, so the runtime role needs narrowly scoped
access to the platform tables involved. No DELETE and no usage_meter access is
granted; business details remain behind tenant RLS.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_provisioning_grants"
down_revision: str | None = "0003_auth_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"


def upgrade() -> None:
    op.create_index("ix_tenant_name", "tenant", ["name"])
    op.execute(f"GRANT SELECT, INSERT ON TABLE tenant TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON TABLE plan TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON TABLE tenant_subscription TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT ON TABLE tenant_subscription FROM {APP_ROLE}")
    op.execute(f"REVOKE SELECT, INSERT ON TABLE plan FROM {APP_ROLE}")
    op.execute(f"REVOKE SELECT, INSERT ON TABLE tenant FROM {APP_ROLE}")
    op.drop_index("ix_tenant_name", table_name="tenant")
