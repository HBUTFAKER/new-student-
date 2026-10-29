"""Seed a local demo tenant and login accounts (local environment only)."""

import asyncio
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.core.security import hash_password

DEMO_PASSWORD = "demo123456"
DEMO_TENANT = "演示机构"
PLATFORM_TENANT = "平台运营"
DEMO_ADMIN_PHONE = "13000000001"
PLATFORM_ADMIN_PHONE = "13000000000"


async def seed() -> None:
    settings = get_settings()
    if settings.app_env != "local":
        raise SystemExit("seed_demo.py only runs with APP_ENV=local")

    engine = create_async_engine(settings.migration_database_url)
    try:
        async with engine.begin() as connection:
            plan_id = await connection.scalar(
                text("SELECT id FROM plan ORDER BY created_at, id LIMIT 1")
            )
            if plan_id is None:
                plan_id = await connection.scalar(
                    text(
                        "INSERT INTO plan (name, max_students, max_messages, price) "
                        "VALUES ('基础版', 100, 1000, :price) RETURNING id"
                    ),
                    {"price": Decimal("0.00")},
                )

            for tenant_name, phone, role in (
                (DEMO_TENANT, DEMO_ADMIN_PHONE, "admin"),
                (PLATFORM_TENANT, PLATFORM_ADMIN_PHONE, "platform_admin"),
            ):
                tenant_id = await connection.scalar(
                    text("SELECT id FROM tenant WHERE name = :name LIMIT 1"),
                    {"name": tenant_name},
                )
                if tenant_id is None:
                    tenant_id = await connection.scalar(
                        text("INSERT INTO tenant (name) VALUES (:name) RETURNING id"),
                        {"name": tenant_name},
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO tenant_subscription "
                            "(tenant_id, plan_id, start_date, end_date, status) "
                            "VALUES (:tenant, :plan, :start, :end, 'active')"
                        ),
                        {
                            "tenant": tenant_id,
                            "plan": plan_id,
                            "start": date.today(),
                            "end": date.today() + timedelta(days=365),
                        },
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO org_config (tenant_id, absence_deduct_rule) "
                            "VALUES (:tenant, 'none')"
                        ),
                        {"tenant": tenant_id},
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO staff_account "
                            "(tenant_id, name, phone, password_hash, role, status) "
                            "VALUES (:tenant, :name, :phone, :hash, :role, 'active')"
                        ),
                        {
                            "tenant": tenant_id,
                            "name": f"{tenant_name}管理员",
                            "phone": phone,
                            "hash": hash_password(DEMO_PASSWORD),
                            "role": role,
                        },
                    )
                print(f"{tenant_name}: tenant_id={tenant_id} phone={phone} role={role}")
    finally:
        await engine.dispose()

    print(f"demo password: {DEMO_PASSWORD}")


if __name__ == "__main__":
    asyncio.run(seed())
