"""Create the first platform_admin account for production (idempotent).

Unlike ``scripts/seed_demo.py`` (local demo data, refuses to run outside
``APP_ENV=local``), this script is production-safe: it only creates a single
platform-admin account + the internal "平台运营" host tenant it belongs to,
reads the phone/password from env or CLI args (never hard-codes them), and is
idempotent — an existing account is skipped, never overwritten.

The password is hashed with the same argon2id helper the app uses
(``app.core.security.hash_password``), so the account logs in through the normal
``POST /auth/login`` flow.

Usage (inside the api container, or anywhere with DB reachability):

    PLATFORM_ADMIN_PHONE=13800000000 \
    PLATFORM_ADMIN_PASSWORD='<strong password>' \
    python scripts/init_platform_admin.py

or with args:

    python scripts/init_platform_admin.py --phone 13800000000 --password '<strong password>'

Run this once on first production boot. Do NOT run seed_demo.py in production.
"""

import argparse
import asyncio
import os
import sys
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.core.security import hash_password

#: platform_admin 的宿主租户名（与 seed_demo.py 保持一致）。
PLATFORM_TENANT_NAME = "平台运营"
#: 平台运营是内部宿主租户，给长期订阅避免它出现在「临期/已到期」列表（7.4 遗留点）。
PLATFORM_TENANT_SUBSCRIPTION_DAYS = 36500


async def _ensure_base_plan(connection) -> str:
    plan_id = await connection.scalar(text("SELECT id FROM plan WHERE name = '基础版' LIMIT 1"))
    if plan_id is not None:
        return plan_id
    return await connection.scalar(
        text(
            "INSERT INTO plan (name, max_students, max_messages, price) "
            "VALUES ('基础版', 100, 1000, :price) RETURNING id"
        ),
        {"price": Decimal("0.00")},
    )


async def _ensure_platform_tenant(connection, plan_id: str) -> str:
    """Return the 平台运营 tenant id, creating it (with subscription + org_config) if absent."""

    tenant_id = await connection.scalar(
        text("SELECT id FROM tenant WHERE name = :name LIMIT 1"), {"name": PLATFORM_TENANT_NAME}
    )
    if tenant_id is not None:
        return tenant_id

    tenant_id = await connection.scalar(
        text("INSERT INTO tenant (name) VALUES (:name) RETURNING id"),
        {"name": PLATFORM_TENANT_NAME},
    )
    await connection.execute(
        text(
            "INSERT INTO tenant_subscription (tenant_id, plan_id, start_date, end_date, status) "
            "VALUES (:tenant, :plan, :start, :end, 'active')"
        ),
        {
            "tenant": tenant_id,
            "plan": plan_id,
            "start": date.today(),
            "end": date.today() + timedelta(days=PLATFORM_TENANT_SUBSCRIPTION_DAYS),
        },
    )
    await connection.execute(
        text("INSERT INTO org_config (tenant_id, absence_deduct_rule) VALUES (:tenant, 'none')"),
        {"tenant": tenant_id},
    )
    return tenant_id


async def init_platform_admin(phone: str, password: str, name: str) -> None:
    settings = get_settings()
    # owner 连接：tenant/plan/tenant_subscription/staff_account 都是平台层表，
    # app_user 无权限，必须走 migration_database_url（与 seed_demo 同口径）。
    engine = create_async_engine(settings.migration_database_url)
    try:
        async with engine.begin() as connection:
            plan_id = await _ensure_base_plan(connection)
            tenant_id = await _ensure_platform_tenant(connection, plan_id)

            existing_role = await connection.scalar(
                text("SELECT role FROM staff_account WHERE phone = :phone LIMIT 1"),
                {"phone": phone},
            )
            if existing_role is not None:
                if existing_role == "platform_admin":
                    print(f"platform_admin already exists (phone={phone}), skipped.")
                else:
                    print(
                        f"phone={phone} already exists with role={existing_role}, "
                        "skipped (not overwriting)."
                    )
                return

            await connection.execute(
                text(
                    "INSERT INTO staff_account "
                    "(tenant_id, name, phone, password_hash, role, status) "
                    "VALUES (:tenant, :name, :phone, :hash, 'platform_admin', 'active')"
                ),
                {
                    "tenant": tenant_id,
                    "name": name,
                    "phone": phone,
                    "hash": hash_password(password),
                },
            )
            print(f"platform_admin created: phone={phone}")
    finally:
        await engine.dispose()


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Create the production platform_admin (idempotent)."
    )
    parser.add_argument(
        "--phone", default=None, help="platform_admin phone (default: $PLATFORM_ADMIN_PHONE)"
    )
    parser.add_argument(
        "--password",
        default=None,
        help="platform_admin password (default: $PLATFORM_ADMIN_PASSWORD)",
    )
    parser.add_argument(
        "--name", default=None, help="display name (default: $PLATFORM_ADMIN_NAME or 平台管理员)"
    )
    args = parser.parse_args()

    phone = args.phone or os.environ.get("PLATFORM_ADMIN_PHONE")
    password = args.password or os.environ.get("PLATFORM_ADMIN_PASSWORD")
    name = args.name or os.environ.get("PLATFORM_ADMIN_NAME") or "平台管理员"

    if not phone or not password:
        print(
            "PLATFORM_ADMIN_PHONE and PLATFORM_ADMIN_PASSWORD are required "
            "(via env or --phone/--password).",
            file=sys.stderr,
        )
        return 2

    asyncio.run(init_platform_admin(phone, password, name))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
