"""org_config add reminder/alert columns + sms_daily_budget default 200 (6.4)

Revision ID: 0015_org_config_scheduler
Revises: 0014_org_config_sms_daily_budget
Create Date: 2026-11-02 01:10:00.000000+08:00

6.4 定时任务的两个机构级配置，加在 ``org_config`` 上（**加列不加表**，表数仍 28 / 业务层 21）：

* ``lesson_reminder_hours TEXT NOT NULL DEFAULT '1440,60'`` —— 上课提醒提前分钟数，
  逗号分隔，空串=不发提醒（发令部拍板：提前 1 天 1440 + 提前 1 小时 60）；
* ``balance_alert_threshold INT NOT NULL DEFAULT 5`` —— 余额预警阈值（≤ 此值时提醒，
  0=关闭预警）；
* ``sms_daily_budget`` 默认 50 → 200（发令部拍板，不许改已提交的 0014，这里 ALTER 改默认）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_org_config_scheduler"
down_revision: str | None = "0014_org_config_sms_daily_budget"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "org_config",
        sa.Column(
            "lesson_reminder_hours",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'1440,60'"),
        ),
    )
    op.add_column(
        "org_config",
        sa.Column(
            "balance_alert_threshold",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("5"),
        ),
    )
    op.alter_column(
        "org_config",
        "sms_daily_budget",
        existing_type=sa.Integer(),
        server_default=sa.text("200"),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "org_config",
        "sms_daily_budget",
        existing_type=sa.Integer(),
        server_default=sa.text("50"),
        existing_nullable=False,
    )
    op.drop_column("org_config", "balance_alert_threshold")
    op.drop_column("org_config", "lesson_reminder_hours")
