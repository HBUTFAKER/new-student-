"""7.7 视觉统一 · 三角色关键页截图。

复用冒烟的 seed 模式：建独立租户 + admin/registrar/teacher 三角色账号 +
学生/班级/课次基础数据，分别登录截 3 张关键页（1366×768），用于人肉比对布局。

运行：``.venv/Scripts/python.exe scripts/screenshot_visual.py``（cwd=项目根）。
前置：PG(55432) + uvicorn(8000) + vite dev(5173) 已启动。

产出：docs/phase0/screenshots/{admin,registrar,teacher}_*.png
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asyncpg  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from app.core.security import hash_password  # noqa: E402

OWNER_DSN = "postgresql://student:student_dev_password@localhost:55432/student"
FRONTEND = "http://localhost:5173"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OUT_DIR = ROOT / "docs" / "phase0" / "screenshots"

UTC8 = timezone(timedelta(hours=8))
TODAY = datetime.now(UTC8).date()


def _phone() -> str:
    return "198" + str(uuid4().int % 10**8).zfill(8)


async def seed() -> dict:
    tenant_id = uuid4()
    plan_id = uuid4()
    teacher_id = uuid4()
    classroom_id = uuid4()
    course_id = uuid4()
    class_id = uuid4()

    admin_phone = _phone()
    reg_phone = _phone()
    teacher_phone = _phone()
    pw = "smoke-123456"

    conn = await asyncpg.connect(OWNER_DSN)
    try:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO tenant (id, name) VALUES ($1, $2)", tenant_id, "视觉冒烟机构"
            )
            await conn.execute(
                "INSERT INTO plan (id, name, max_students, max_messages, price) "
                "VALUES ($1,$2,$3,$4,$5)",
                plan_id,
                "基础版",
                100,
                1000,
                0,
            )
            await conn.execute(
                "INSERT INTO tenant_subscription (id, tenant_id, plan_id, "
                "start_date, end_date, status) VALUES ($1,$2,$3,$4,$5,'active')",
                uuid4(),
                tenant_id,
                plan_id,
                TODAY,
                datetime(2099, 12, 31).date(),
            )
            await conn.execute(
                "INSERT INTO teacher (id, tenant_id, name, subject) VALUES ($1,$2,$3,$4)",
                teacher_id,
                tenant_id,
                "李老师",
                "数学",
            )
            # 三个角色账号
            for sid, name, role, tid in [
                (uuid4(), "管理员", "admin", None),
                (uuid4(), "教务", "registrar", None),
                (uuid4(), "李老师", "teacher", teacher_id),
            ]:
                await conn.execute(
                    "INSERT INTO staff_account (id, tenant_id, name, phone, password_hash, "
                    "role, teacher_id, status) VALUES ($1,$2,$3,$4,$5,$6,$7,'active')",
                    sid,
                    tenant_id,
                    name,
                    {"admin": admin_phone, "registrar": reg_phone, "teacher": teacher_phone}[role],
                    hash_password(pw),
                    role,
                    tid,
                )
            # 教室 + 课程 + 班级
            await conn.execute(
                "INSERT INTO classroom (id, tenant_id, name, capacity) "
                "VALUES ($1,$2,'101 教室',20)",
                classroom_id,
                tenant_id,
            )
            await conn.execute(
                "INSERT INTO course (id, tenant_id, name, subject, duration_min, status) "
                "VALUES ($1,$2,'数学冲刺','数学',90,'active')",
                course_id,
                tenant_id,
            )
            await conn.execute(
                "INSERT INTO class (id, tenant_id, course_id, teacher_id, classroom_id, "
                "name, capacity, term_start, term_end, status) "
                "VALUES ($1,$2,$3,$4,$5,'冲刺A班',12,$6,$7,'active')",
                class_id,
                tenant_id,
                course_id,
                teacher_id,
                classroom_id,
                TODAY,
                TODAY + timedelta(days=90),
            )
            # 3 名学生 + 入班
            for name in ["张小明", "王小华", "陈小雨"]:
                sid = uuid4()
                await conn.execute(
                    "INSERT INTO student (id, tenant_id, name, status) VALUES ($1,$2,$3,'active')",
                    sid,
                    tenant_id,
                    name,
                )
                await conn.execute(
                    "INSERT INTO class_enrollment (id, tenant_id, class_id, student_id, joined_at) "
                    "VALUES ($1,$2,$3,$4,$5)",
                    uuid4(),
                    tenant_id,
                    class_id,
                    sid,
                    TODAY,
                )

            # 今天 2 节 + 明天 1 节课（排课日历有内容）
            def _dt(day_offset: int, hh: int, mm: int) -> datetime:
                return datetime(
                    TODAY.year, TODAY.month, TODAY.day, hh, mm, tzinfo=UTC8
                ) + timedelta(days=day_offset)

            for start, end in [
                (_dt(0, 18, 0), _dt(0, 19, 30)),
                (_dt(0, 20, 0), _dt(0, 21, 0)),
                (_dt(1, 18, 0), _dt(1, 19, 30)),
            ]:
                await conn.execute(
                    "INSERT INTO lesson (id, tenant_id, class_id, classroom_id, teacher_id, "
                    "start_at, end_at, status) VALUES ($1,$2,$3,$4,$5,$6,$7,'normal')",
                    uuid4(),
                    tenant_id,
                    class_id,
                    classroom_id,
                    teacher_id,
                    start,
                    end,
                )
    finally:
        await conn.close()

    return {"admin": admin_phone, "registrar": reg_phone, "teacher": teacher_phone, "pw": pw}


def _login(page, phone: str, pw: str) -> None:
    page.goto(f"{FRONTEND}/login")
    page.get_by_placeholder("请输入手机号", exact=True).fill(phone)
    page.get_by_placeholder("请输入密码", exact=True).fill(pw)
    page.get_by_role("button", name="登录").click()
    page.wait_for_url("**/dashboard", timeout=15000)


def _shot(page, path: str) -> None:
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1200)
    page.screenshot(path=path, full_page=False)


def main() -> None:
    creds = asyncio.run(seed())
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    pages = {
        "admin": ["/dashboard", "/archive/students", "/scheduling/timetable"],
        "registrar": ["/dashboard", "/classes", "/reports/attendance"],
        "teacher": ["/dashboard", "/my-schedule", "/attendance"],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=False)
        try:
            for role, urls in pages.items():
                ctx = browser.new_context(viewport={"width": 1366, "height": 768})
                page = ctx.new_page()
                _login(page, creds[role], creds["pw"])
                for i, url in enumerate(urls, 1):
                    page.goto(f"{FRONTEND}{url}")
                    out = OUT_DIR / f"{role}_{i}_{url.strip('/').replace('/', '_')}.png"
                    _shot(page, str(out))
                    print(f"✔ {out.name}")
                ctx.close()
        finally:
            browser.close()

    print(f"\n截图已保存到 {OUT_DIR}")


if __name__ == "__main__":
    main()
