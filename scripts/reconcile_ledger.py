"""Cross-tenant ledger reconciliation (step 2.4).

The balance is never stored, so "对账" means: does the service's live package
view still agree with the raw ledger? Three independent checks per tenant:

1. every package's ``remaining`` equals ``SUM(delta)`` of the rows carrying that
   ``purchase_id``;
2. the student total equals the sum of the package ``remaining`` values, so no
   ledger row is orphaned outside a package;
3. only the row shapes this step can produce exist: ``consume`` carries both
   ``purchase_id`` and ``lesson_id``, while ``purchase`` / ``grant`` carry a
   ``purchase_id``.

Run with the runtime role (``DATABASE_URL``) and iterate tenants through the
same transaction-local GUC the API uses, so the script exercises the RLS path
instead of bypassing it::

    python -m scripts.reconcile_ledger          # human readable
    python -m scripts.reconcile_ledger --json   # machine readable

Exit code 0 means zero drift, 1 means mismatches were found.
"""

import argparse
import asyncio
import json
import sys

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.tenant import tenant_context
from app.models.ledger import LessonLedger
from app.services.ledger import student_packages, total_balance

CHECKED_TYPES = ("purchase", "grant", "consume", "adjust")


async def _check_tenant(db: AsyncSession) -> dict:
    student_ids = (await db.scalars(select(LessonLedger.student_id).distinct())).all()
    totals = dict(
        (
            await db.execute(
                select(
                    LessonLedger.student_id,
                    func.sum(LessonLedger.delta),
                ).group_by(LessonLedger.student_id)
            )
        ).all()
    )
    per_purchase = dict(
        (
            await db.execute(
                select(LessonLedger.purchase_id, func.sum(LessonLedger.delta))
                .where(LessonLedger.purchase_id.is_not(None))
                .group_by(LessonLedger.purchase_id)
            )
        ).all()
    )
    bad_rows = (
        (
            await db.execute(
                select(LessonLedger)
                .where(LessonLedger.type.in_(CHECKED_TYPES))
                .where(
                    (LessonLedger.purchase_id.is_(None))
                    | ((LessonLedger.type == "consume") & (LessonLedger.lesson_id.is_(None)))
                )
            )
        )
        .scalars()
        .all()
    )

    report = {
        "students": 0,
        "purchases": 0,
        "entries": int(await db.scalar(select(func.count()).select_from(LessonLedger)) or 0),
        "mismatches": [],
    }
    for student_id in student_ids:
        packages = await student_packages(db, student_id)
        ledger_total = int(totals.get(student_id, 0) or 0)
        package_total = sum(item["remaining"] for item in packages)
        report["students"] += 1
        report["purchases"] += len(packages)
        if ledger_total != package_total:
            report["mismatches"].append(
                {
                    "kind": "student_total",
                    "student_id": str(student_id),
                    "ledger_total": ledger_total,
                    "package_total": package_total,
                }
            )
        recomputed = await total_balance(db, student_id)
        if recomputed != ledger_total:
            report["mismatches"].append(
                {
                    "kind": "student_recompute",
                    "student_id": str(student_id),
                    "expected": ledger_total,
                    "actual": recomputed,
                }
            )
        seen: set = set()
        for item in packages:
            seen.add(item["purchase_id"])
            expected = int(per_purchase.get(item["purchase_id"], 0) or 0)
            if expected != item["remaining"]:
                report["mismatches"].append(
                    {
                        "kind": "package_remaining",
                        "student_id": str(student_id),
                        "purchase_id": str(item["purchase_id"]),
                        "ledger_sum": expected,
                        "package_remaining": item["remaining"],
                    }
                )
        for purchase_id, expected in per_purchase.items():
            if purchase_id is not None and purchase_id not in seen:
                report["mismatches"].append(
                    {
                        "kind": "orphan_purchase",
                        "student_id": str(student_id),
                        "purchase_id": str(purchase_id),
                        "ledger_sum": int(expected or 0),
                    }
                )
    for row in bad_rows:
        report["mismatches"].append(
            {
                "kind": "row_shape",
                "entry_id": str(row.id),
                "type": row.type,
                "purchase_id": None if row.purchase_id is None else str(row.purchase_id),
                "lesson_id": None if row.lesson_id is None else str(row.lesson_id),
            }
        )
    return report


async def reconcile() -> dict:
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    summary = {
        "tenants": 0,
        "students": 0,
        "purchases": 0,
        "entries": 0,
        "diff_count": 0,
        "mismatches": [],
    }
    try:
        async with factory() as session:
            tenant_ids = (await session.scalars(text("SELECT id FROM tenant ORDER BY id"))).all()
        for tenant_id in tenant_ids:
            async with factory() as session, tenant_context(session, tenant_id):
                report = await _check_tenant(session)
            summary["tenants"] += 1
            summary["students"] += report["students"]
            summary["purchases"] += report["purchases"]
            summary["entries"] += report["entries"]
            for item in report["mismatches"]:
                summary["mismatches"].append({"tenant_id": str(tenant_id), **item})
        summary["diff_count"] = len(summary["mismatches"])
    finally:
        await engine.dispose()
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="课时账本全租户对账")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    summary = asyncio.run(reconcile())
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, default=str, indent=2))
    else:
        print(
            f"租户 {summary['tenants']} / 学生 {summary['students']} / "
            f"课时包 {summary['purchases']} / 流水 {summary['entries']}，"
            f"差异 {summary['diff_count']} 条"
        )
        for item in summary["mismatches"]:
            print(f"  - {item}")
    return 0 if summary["diff_count"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
