"""org_config add sms_daily_budget (6.3)

Revision ID: 0014_org_config_sms_daily_budget
Revises: 0013_notification
Create Date: 2026-11-02 00:30:00.000000+08:00

6.3 短信通道的机构级日预算：发送前统计当日该租户短信成功条数（COUNT ``notification``
里 ``'sms' = ANY(succeeded_channels)`` 且 ``updated_at`` 落在当日），达到预算即跳过短信级。
``sms_daily_budget INT NOT NULL DEFAULT 50`` 加在 ``org_config`` 上，**加列不加表**，表数
仍 28（业务层 21）。10 人机构无并发风险，COUNT 统计即可，不为预算加表。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_org_config_sms_daily_budget"
down_revision: str | None = "0013_notification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "org_config",
        sa.Column("sms_daily_budget", sa.Integer(), nullable=False, server_default=sa.text("50")),
    )


def downgrade() -> None:
    op.drop_column("org_config", "sms_daily_budget")
