"""RLS 覆盖审计（7.5 任务二，进 CI 常跑）。

业务层 21 张表 + ``subscribe_auth`` 必须全部启用 RLS 且至少一条 policy。
逮到「有 tenant_id 却没 RLS」的表 = 洞（口径 2），立即红。

白名单 = 平台层表：``tenant_subscription`` / ``usage_meter`` 有 tenant_id 但
跨租户聚合（0.3 决策 6：platform_admin 要跨租户看概览，无单租户隔离语义）；
``tenant`` / ``plan`` / ``wechat_user`` / ``wechat_openid`` 根本无 tenant_id。

白名单逐表写理由；「报错就加白名单」是本测试的死敌——写不出理由按洞处理。
"""

import os

import pytest
from sqlalchemy import text

from app.models import RLS_TABLES

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run the RLS coverage audit",
    ),
]

#: 有 tenant_id 但故意不启 RLS 的平台层表（白名单，逐表理由）。
RLS_EXEMPT: dict[str, str] = {
    "tenant_subscription": (
        "平台层订阅表：platform_admin 跨租户聚合租户订阅与到期日，无单租户隔离语义"
    ),
    "usage_meter": (
        "平台层计量表：owner 连接跨租户累计用量（7.3 学生/消息限额），无单租户隔离语义"
    ),
}

#: 无 tenant_id 的层：平台层 tenant/plan + 微信身份 wechat_user/wechat_openid。
TENANTLESS_TABLES: tuple[str, ...] = (
    "tenant",
    "plan",
    "wechat_user",
    "wechat_openid",
)


async def _scalar_set(conn, sql: str) -> set[str]:
    return set((await conn.execute(text(sql))).scalars())


async def test_business_layer_fully_rls_protected(owner_engine) -> None:
    """每一张业务层表（+ subscribe_auth）都必须有 tenant_id + RLS + policy。"""

    async with owner_engine.connect() as conn:
        with_tenant_id = await _scalar_set(
            conn,
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'tenant_id' "
            "GROUP BY table_name",
        )
        with_rls = await _scalar_set(
            conn,
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity",
        )
        with_policy = await _scalar_set(
            conn,
            "SELECT DISTINCT tablename FROM pg_policies WHERE schemaname = 'public'",
        )

    expected_tenant_id = set(RLS_TABLES) | set(RLS_EXEMPT)

    # 1. 带 tenant_id 的表必须恰好 = 业务层 + subscribe_auth + 白名单。
    assert with_tenant_id == expected_tenant_id, (
        "带 tenant_id 的表与预期不符：\n"
        f"  多出（有 tenant_id 却未登记 = 洞）：{sorted(with_tenant_id - expected_tenant_id)}\n"
        f"  缺失（应有 tenant_id 却没有）：{sorted(expected_tenant_id - with_tenant_id)}"
    )

    # 2. 业务层 + subscribe_auth 每一张都启用 RLS 且至少一条 policy。
    assert not (set(RLS_TABLES) - with_rls), (
        f"应启用 RLS 却未启用的表（洞）：{sorted(set(RLS_TABLES) - with_rls)}"
    )
    assert not (set(RLS_TABLES) - with_policy), (
        f"启用了 RLS 却没有 policy 的表：{sorted(set(RLS_TABLES) - with_policy)}"
    )

    # 3. 白名单表必须有 tenant_id 但无 RLS（理由见 RLS_EXEMPT）。
    assert not (set(RLS_EXEMPT) & with_rls), (
        f"白名单表不应启用 RLS 却启用了：{sorted(set(RLS_EXEMPT) & with_rls)}"
    )

    # 4. 异常状态别静默：无 tenant_id 却启用了 RLS 的表要报告。
    assert not (with_rls - with_tenant_id), (
        f"无 tenant_id 却启用了 RLS 的表（异常状态）：{sorted(with_rls - with_tenant_id)}"
    )


async def test_platform_and_wechat_identity_tables_are_tenantless(owner_engine) -> None:
    """平台层 tenant/plan 与微信身份 wechat_user/wechat_openid 不该有 tenant_id 列。"""

    async with owner_engine.connect() as conn:
        for table in TENANTLESS_TABLES:
            has_tenant_id = await conn.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :t "
                    "AND column_name = 'tenant_id')"
                ),
                {"t": table},
            )
            assert not has_tenant_id, f"{table} 不应带 tenant_id 列"


async def test_rls_policies_use_the_tenant_guc_predicate(owner_engine) -> None:
    """每一张 RLS 表的 policy 都锚定 app.tenant_id GUC（不能是恒真的兜底 policy）。"""

    async with owner_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT tablename, policyname, qual FROM pg_policies "
                    "WHERE schemaname = 'public' ORDER BY tablename, policyname"
                )
            )
        ).all()

    policies_by_table: dict[str, list[tuple[str, str]]] = {}
    for tablename, policyname, qual in rows:
        policies_by_table.setdefault(tablename, []).append((policyname, qual or ""))

    for table in RLS_TABLES:
        assert table in policies_by_table, f"{table} 没有登记任何 policy"
        quals = " || ".join(qual for _, qual in policies_by_table[table])
        assert "app.tenant_id" in quals, (
            f"{table} 的 policy 没有锚定 app.tenant_id GUC，可能恒真：{policies_by_table[table]}"
        )
