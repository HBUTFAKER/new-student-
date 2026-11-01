"""端到端冒烟脚本（040 补账 + 7.2 到期降级）。

前置：本地 PG（localhost:55432）+ uvicorn 真后端（:8000）+ vite dev（:5173）已启动。
运行：``.venv/Scripts/python.exe scripts/smoke_playwright.py``（cwd=项目根）。

覆盖场景（≥8 断言，全程 0 条控制台报错）：
  1. admin 新建员工账号 → 列表出现新行
  2. 停用员工 → 二次确认弹窗 → 确认后状态变「停用」
  3. teacher 直输 /staff 路由 → 被拦 403
  4. teacher 顶栏「注销我的账号」→ 输密码确认 → 跳登录页且再登录 401「账号已停用」
  5. 7.2：改库 end_date=昨天 → 刷新出现红色降级横幅 → 保存写操作报 402 文案
     → 改回未来日期 → 横幅消失、写操作成功

每次运行生成唯一手机号 + 独立租户，脚本幂等，不污染既有数据。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asyncpg  # noqa: E402  — 需先 sys.path 注入项目根再导入 app
from playwright.sync_api import Page, expect, sync_playwright  # noqa: E402

from app.core.security import hash_password  # noqa: E402

OWNER_DSN = "postgresql://student:student_dev_password@localhost:55432/student"
FRONTEND = "http://localhost:5173"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

UTC8 = timezone(timedelta(hours=8))
TODAY = datetime.now(UTC8).date()
YESTERDAY = TODAY - timedelta(days=1)
FUTURE = TODAY + timedelta(days=30)

# 统计用
CHECKS: list[str] = []
CONSOLE_ERRORS: list[str] = []
PAGE_ERRORS: list[str] = []


def check(name: str, condition: bool) -> None:
    """记录一条断言并计数（失败时抛异常，由 pytest 风格报告定位）。"""

    CHECKS.append(name)
    if not condition:
        raise AssertionError(f"断言失败：{name}")


def _gen_phone() -> str:
    return "199" + str(uuid4().int % 10**8).zfill(8)


def _run_async(coro):
    """在独立线程跑一个协程——Playwright sync API 主线程已有事件循环，不能再 asyncio.run。"""

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


# ---------------------------------------------------------------- 种子数据


async def seed() -> dict:
    """用 owner 连接直接建机构 + admin/teacher 账号 + 订阅（2099 到期）。"""

    tenant_id = uuid4()
    admin_phone = _gen_phone()
    teacher_phone = _gen_phone()
    admin_pass = "smoke-admin-123456"
    teacher_pass = "smoke-teacher-123456"

    plan_id = uuid4()
    teacher_id = uuid4()
    admin_staff_id = uuid4()
    teacher_staff_id = uuid4()

    conn = await asyncpg.connect(OWNER_DSN)
    try:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO tenant (id, name) VALUES ($1, $2)",
                tenant_id,
                "冒烟测试机构",
            )
            await conn.execute(
                "INSERT INTO plan (id, name, max_students, max_messages, price) "
                "VALUES ($1, $2, $3, $4, $5)",
                plan_id,
                "基础版",
                100,
                1000,
                0,
            )
            await conn.execute(
                "INSERT INTO tenant_subscription "
                "(id, tenant_id, plan_id, start_date, end_date, status) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                uuid4(),
                tenant_id,
                plan_id,
                TODAY,
                date(2099, 12, 31),
                "active",
            )
            await conn.execute(
                "INSERT INTO teacher (id, tenant_id, name) VALUES ($1, $2, $3)",
                teacher_id,
                tenant_id,
                "冒烟老师",
            )
            await conn.execute(
                "INSERT INTO staff_account "
                "(id, tenant_id, name, phone, password_hash, role, teacher_id, status) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                admin_staff_id,
                tenant_id,
                "冒烟管理员",
                admin_phone,
                hash_password(admin_pass),
                "admin",
                None,
                "active",
            )
            await conn.execute(
                "INSERT INTO staff_account "
                "(id, tenant_id, name, phone, password_hash, role, teacher_id, status) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                teacher_staff_id,
                tenant_id,
                "冒烟老师账号",
                teacher_phone,
                hash_password(teacher_pass),
                "teacher",
                teacher_id,
                "active",
            )
    finally:
        await conn.close()

    return {
        "tenant_id": str(tenant_id),
        "admin_phone": admin_phone,
        "admin_pass": admin_pass,
        "teacher_phone": teacher_phone,
        "teacher_pass": teacher_pass,
    }


async def set_end_date(tenant_id: str, end: date) -> None:
    conn = await asyncpg.connect(OWNER_DSN)
    try:
        await conn.execute(
            "UPDATE tenant_subscription SET end_date = $1 WHERE tenant_id = $2",
            end,
            tenant_id,
        )
    finally:
        await conn.close()


async def set_plan_max_students(tenant_id: str, max_students: int) -> None:
    """7.3：改库套餐学生上限（改大/改小），验证限额即时生效、无缓存。"""

    conn = await asyncpg.connect(OWNER_DSN)
    try:
        await conn.execute(
            "UPDATE plan SET max_students = $1 WHERE id = "
            "(SELECT plan_id FROM tenant_subscription WHERE tenant_id = $2)",
            max_students,
            tenant_id,
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------- 浏览器交互


def _login(page: Page, phone: str, password: str) -> None:
    page.goto(f"{FRONTEND}/login")
    page.get_by_placeholder("请输入手机号", exact=True).fill(phone)
    page.get_by_placeholder("请输入密码", exact=True).fill(password)
    page.get_by_role("button", name="登录").click()
    page.wait_for_url("**/dashboard", timeout=15000)


def _open_create_dialog(page: Page) -> Page:
    page.goto(f"{FRONTEND}/staff")
    page.get_by_role("button", name="新建账号").first.click()
    dialog = page.locator(".el-dialog:visible", has_text="新建员工账号")
    dialog.wait_for(state="visible")
    return dialog


def _fill_create_form(dialog: Page, name: str, phone: str, password: str) -> None:
    dialog.get_by_placeholder("必填", exact=True).fill(name)
    dialog.get_by_placeholder("11 位手机号，必填", exact=True).fill(phone)
    dialog.locator(".el-radio-button").filter(has_text="教务").click()
    dialog.get_by_placeholder("8~72 字符", exact=True).fill(password)


def _submit_create(dialog: Page) -> None:
    dialog.get_by_role("button", name="创建").click()


def run(creds: dict) -> None:
    def track(page: Page) -> None:
        page.on("console", lambda m: CONSOLE_ERRORS.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: PAGE_ERRORS.append(str(e)))

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=EDGE,
            headless=False,  # 本地冒烟：肉眼可见（无头下 Edge 某些企业策略会拦）
            slow_mo=120,
        )

        try:
            admin_ctx = browser.new_context()
            admin = admin_ctx.new_page()
            track(admin)

            # ---------------------------------------------------------------- 场景 1：新建员工
            _login(admin, creds["admin_phone"], creds["admin_pass"])
            new_name = "冒烟新员工"
            new_phone = _gen_phone()
            dialog = _open_create_dialog(admin)
            _fill_create_form(dialog, new_name, new_phone, "smoke-staff-123456")
            _submit_create(dialog)
            admin.get_by_text("账号已创建").wait_for(state="visible", timeout=8000)
            expect(admin.get_by_role("cell", name=new_name)).to_have_count(1)
            check("场景1: 新建员工后列表出现新行", True)

            # ---------------------------------------------------------------- 场景 2：停用二次确认
            row = admin.locator("tr").filter(has_text=new_name)
            row.get_by_role("button", name="停用").click()
            confirm = admin.locator(".el-message-box")
            confirm.wait_for(state="visible")
            check("场景2: 二次确认弹窗出现", "停用账号" in confirm.inner_text())
            confirm.get_by_role("button", name="确认停用").click()
            admin.get_by_text("已停用").wait_for(state="visible", timeout=8000)
            row2 = admin.locator("tr").filter(has_text=new_name)
            expect(row2.locator(".el-tag").get_by_text("停用")).to_have_count(1)
            check("场景2: 确认后状态变「停用」", True)

            # ---------------------------------------------------------------- 场景 5：7.2 到期降级
            _run_async(set_end_date(creds["tenant_id"], YESTERDAY))
            admin.goto(f"{FRONTEND}/dashboard")
            banner = admin.locator(".layout__banner")
            banner.wait_for(state="visible", timeout=8000)
            check("场景5: 到期后出现红色降级横幅", "服务已到期" in banner.inner_text())

            dialog = _open_create_dialog(admin)
            _fill_create_form(dialog, "冒烟只读阻断", _gen_phone(), "smoke-readonly-123")
            _submit_create(dialog)
            admin.get_by_text("机构服务已到期").first.wait_for(state="visible", timeout=8000)
            check("场景5: 写操作被 402 拦截并提示到期文案", True)

            _run_async(set_end_date(creds["tenant_id"], FUTURE))
            admin.goto(f"{FRONTEND}/dashboard")
            expect(admin.locator(".layout__banner")).to_have_count(0)
            check("场景5: 续费(改回未来)后横幅消失", True)

            dialog = _open_create_dialog(admin)
            recover_name = "冒烟恢复员工"
            _fill_create_form(dialog, recover_name, _gen_phone(), "smoke-recover-123")
            _submit_create(dialog)
            admin.get_by_text("账号已创建").wait_for(state="visible", timeout=8000)
            expect(admin.get_by_role("cell", name=recover_name)).to_have_count(1)
            check("场景5: 恢复后写操作成功", True)

            # ---------------------------------------------------------------- 场景 6：7.3 学生限额
            _run_async(set_plan_max_students(creds["tenant_id"], 1))
            admin.goto(f"{FRONTEND}/archive/students")
            admin.get_by_role("button", name="新建学生").click()
            sdlg = admin.locator(".el-dialog:visible", has_text="新建学生")
            sdlg.wait_for(state="visible")
            sdlg.get_by_placeholder("必填", exact=True).fill("限额学生一")
            sdlg.get_by_role("button", name="保存").click()
            admin.get_by_text("已新建").wait_for(state="visible", timeout=8000)
            check("场景6: max_students=1 建第 1 个学生成功", True)

            # 重载触发 onMounted 拉 /me/subscription → 1/1 已达 100% → 黄色接近上限提示
            admin.goto(f"{FRONTEND}/archive/students")
            admin.get_by_text("已接近套餐上限").wait_for(state="visible", timeout=8000)
            check("场景6: 接近上限出现黄色提示条", True)

            # 第 2 个学生撞 402，文案正确
            admin.get_by_role("button", name="新建学生").click()
            sdlg2 = admin.locator(".el-dialog:visible", has_text="新建学生")
            sdlg2.wait_for(state="visible")
            sdlg2.get_by_placeholder("必填", exact=True).fill("限额学生二")
            sdlg2.get_by_role("button", name="保存").click()
            admin.get_by_text("学生数已达套餐上限 1 人").first.wait_for(
                state="visible", timeout=8000
            )
            check("场景6: 第 2 个学生撞 402 并提示限额文案", True)
            expect(admin.get_by_role("cell", name="限额学生二")).to_have_count(0)
            check("场景6: 402 后学生未超建（列表仍只有 1 人）", True)

            # 改大 → 立即恢复（无缓存）
            _run_async(set_plan_max_students(creds["tenant_id"], 100))
            sdlg2.get_by_role("button", name="保存").click()
            admin.get_by_text("已新建").wait_for(state="visible", timeout=8000)
            expect(admin.get_by_role("cell", name="限额学生二")).to_have_count(1)
            check("场景6: 改大后第 2 个学生创建成功", True)

            # 改大后重载：2/100 未达 90% 阈值 → 黄色提示条消失
            admin.goto(f"{FRONTEND}/archive/students")
            admin.get_by_role("cell", name="限额学生一").wait_for(state="visible", timeout=8000)
            expect(admin.get_by_text("已接近套餐上限")).to_have_count(0)
            check("场景6: 改大后黄色提示条消失", True)

            admin_ctx.close()

            # ---------------------------------------------------------------- 场景 3/4：teacher
            teacher_ctx = browser.new_context()
            teacher = teacher_ctx.new_page()
            track(teacher)

            _login(teacher, creds["teacher_phone"], creds["teacher_pass"])
            teacher.goto(f"{FRONTEND}/staff")
            teacher.get_by_text("403 · 无权访问").wait_for(state="visible", timeout=10000)
            check("场景3: teacher 直输 /staff 被拦 403", "/403" in teacher.url)

            teacher.goto(f"{FRONTEND}/dashboard")
            teacher.locator(".layout__account--btn").click()
            teacher.get_by_text("注销我的账号").click()
            deact_dialog = teacher.locator(".el-dialog:visible", has_text="注销我的账号")
            deact_dialog.wait_for(state="visible")
            deact_dialog.get_by_placeholder("验证身份后注销").fill(creds["teacher_pass"])
            deact_dialog.get_by_role("button", name="确认注销").click()
            teacher.get_by_role("button", name="登录").wait_for(state="visible", timeout=10000)
            check("场景4: 注销后跳转登录页", "/login" in teacher.url)

            # 用已注销账号再登录 → 401「账号已停用」
            teacher.get_by_placeholder("请输入手机号", exact=True).fill(creds["teacher_phone"])
            teacher.get_by_placeholder("请输入密码", exact=True).fill(creds["teacher_pass"])
            teacher.get_by_role("button", name="登录").click()
            teacher.get_by_text("账号已停用").wait_for(state="visible", timeout=8000)
            check("场景4: 注销后再登录被 401 拒绝", True)

            teacher_ctx.close()
        finally:
            browser.close()


def main() -> None:
    creds = asyncio.run(seed())
    print(
        f"[seed] tenant={creds['tenant_id']} "
        f"admin={creds['admin_phone']} teacher={creds['teacher_phone']}"
    )
    run(creds)

    print("\n===== 冒烟结果 =====")
    print(f"断言通过 {len(CHECKS)} 条：")
    for name in CHECKS:
        print(f"  ✔ {name}")

    # 控制台报错过滤：忽略已知良性噪音（favicon 404 等）
    benign = ("favicon", "Failed to load resource")
    real_console = [e for e in CONSOLE_ERRORS if not any(b in e for b in benign)]
    if PAGE_ERRORS or real_console:
        print("\n⚠ 控制台报错：")
        for e in real_console:
            print(f"  [console] {e}")
        for e in PAGE_ERRORS:
            print(f"  [pageerror] {e}")
        sys.exit(1)
    print("控制台报错 0 条 ✔")


if __name__ == "__main__":
    main()
