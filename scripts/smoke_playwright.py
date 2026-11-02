"""端到端冒烟脚本（040 补账 + 7.2 到期降级 + 047 课时账本/退费强确认/结课 + 048 课后反馈）。

前置：本地 PG（localhost:55432）+ uvicorn 真后端（:8000）+ vite dev（:5173）已启动。
运行：``.venv/Scripts/python.exe scripts/smoke_playwright.py``（cwd=项目根）。

覆盖场景（≥30 断言，全程 0 条控制台报错）：
  1. admin 新建员工账号 → 列表出现新行
  2. 停用员工 → 二次确认弹窗 → 确认后状态变「停用」
  3. teacher 直输 /staff 路由 → 被拦 403
  4. teacher 顶栏「注销我的账号」→ 输密码确认 → 跳登录页且再登录 401「账号已停用」
  5. 7.2：改库 end_date=昨天 → 刷新出现红色降级横幅 → 保存写操作报 402 文案
     → 改回未来日期 → 横幅常显(次要色「距到期还有 N 天」)、写操作成功
  6. 7.3：改库 max_students=1 → 建 1 人成功 → 黄条 → 第 2 人 402 → 改大恢复
  7. 7.4：platform_admin 登录落平台后台 → 三卡 → 过期机构详情用量 → 续费 30 天 tag 红变绿
     → 机构 admin 直输 /platform/tenants 被拦 403
  8. 047：学生列表「账本」入口 → 购课登记余额 0→10 → 退费两步强确认四要素完整
     → 确认后余额 10→9 且流水出现「退费」行
  9. 047：班级「结课」二次确认 → 状态 tag 变「已结课」（灰）且出现「恢复」入口
  10. 048：课次详情「课后反馈」→ 填写保存 → 重进内容正确显示 → 编辑更新为最新

每次运行生成唯一手机号 + 独立租户，脚本幂等，不污染既有数据。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import sys
from datetime import date, datetime, time, timedelta, timezone
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

    # 047 账本/交易域 + 结课场景：课程/教室/班级（唯一名，供 UI 定位）。
    # 注意：学生不在 seed 里建——7.3 限额场景依赖主租户从 0 学生起算，账本学生在 场景 8 经 UI 新建。
    course_id = uuid4()
    classroom_id = uuid4()
    class_id = uuid4()
    course_name = f"冒烟课程-{uuid4().hex[:6]}"
    class_name = f"冒烟结课班-{uuid4().hex[:6]}"

    # 048 课堂反馈场景：独立 active 班 + 学员 + 本周课次（别用结课班，避免被场景 9 结课污染）。
    fb_class_id = uuid4()
    fb_class_name = f"冒烟反馈班-{uuid4().hex[:6]}"
    fb_student_ids = [uuid4(), uuid4()]
    fb_lesson_id = uuid4()
    fb_start = datetime.combine(TODAY, time(9, 0), tzinfo=UTC8)
    fb_end = datetime.combine(TODAY, time(10, 0), tzinfo=UTC8)

    # 7.4 平台后台：platform_admin 账号 + 一个过期机构（用于续费场景）
    platform_phone = _gen_phone()
    platform_pass = "smoke-platform-123456"
    platform_tenant_id = uuid4()
    platform_staff_id = uuid4()
    expired_tenant_id = uuid4()
    expired_plan_id = uuid4()
    expired_name = f"冒烟过期机构-{uuid4().hex[:6]}"
    expired_students = [uuid4(), uuid4()]

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

            # ---- 047 账本/交易域 + 结课种子 ----
            await conn.execute(
                "INSERT INTO course (id, tenant_id, name, status) VALUES ($1, $2, $3, 'active')",
                course_id,
                tenant_id,
                course_name,
            )
            await conn.execute(
                "INSERT INTO classroom (id, tenant_id, name, capacity) VALUES ($1, $2, $3, $4)",
                classroom_id,
                tenant_id,
                "冒烟教室",
                30,
            )
            await conn.execute(
                "INSERT INTO class "
                "(id, tenant_id, course_id, teacher_id, classroom_id, name, capacity, status) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, 'active')",
                class_id,
                tenant_id,
                course_id,
                teacher_id,
                classroom_id,
                class_name,
                20,
            )
            # ---- 048 课堂反馈场景：反馈班 + 学员 + 本周课次 ----
            await conn.execute(
                "INSERT INTO class "
                "(id, tenant_id, course_id, teacher_id, classroom_id, name, capacity, status) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, 'active')",
                fb_class_id,
                tenant_id,
                course_id,
                teacher_id,
                classroom_id,
                fb_class_name,
                20,
            )
            for idx, sid in enumerate(fb_student_ids):
                await conn.execute(
                    "INSERT INTO student (id, tenant_id, name, status) "
                    "VALUES ($1, $2, $3, 'active')",
                    sid,
                    tenant_id,
                    f"冒烟反馈学生{idx + 1}",
                )
                await conn.execute(
                    "INSERT INTO class_enrollment (id, tenant_id, class_id, student_id, joined_at) "
                    "VALUES ($1, $2, $3, $4, $5)",
                    uuid4(),
                    tenant_id,
                    fb_class_id,
                    sid,
                    TODAY,
                )
            await conn.execute(
                "INSERT INTO lesson "
                "(id, tenant_id, class_id, classroom_id, teacher_id, start_at, end_at, status) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, 'normal')",
                fb_lesson_id,
                tenant_id,
                fb_class_id,
                classroom_id,
                teacher_id,
                fb_start,
                fb_end,
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

            # ---- 7.4 平台后台种子：platform_admin（宿主租户）+ 过期机构 + 2 学生 ----
            await conn.execute(
                "INSERT INTO tenant (id, name) VALUES ($1, $2)",
                platform_tenant_id,
                "平台运营-冒烟",
            )
            await conn.execute(
                "INSERT INTO tenant_subscription "
                "(id, tenant_id, plan_id, start_date, end_date, status) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                uuid4(),
                platform_tenant_id,
                plan_id,
                TODAY,
                date(2099, 12, 31),
                "active",
            )
            await conn.execute(
                "INSERT INTO staff_account "
                "(id, tenant_id, name, phone, password_hash, role, teacher_id, status) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                platform_staff_id,
                platform_tenant_id,
                "平台运营管理员",
                platform_phone,
                hash_password(platform_pass),
                "platform_admin",
                None,
                "active",
            )

            await conn.execute(
                "INSERT INTO tenant (id, name) VALUES ($1, $2)",
                expired_tenant_id,
                expired_name,
            )
            await conn.execute(
                "INSERT INTO plan (id, name, max_students, max_messages, price) "
                "VALUES ($1, $2, $3, $4, $5)",
                expired_plan_id,
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
                expired_tenant_id,
                expired_plan_id,
                TODAY - timedelta(days=60),
                YESTERDAY,
                "active",
            )
            for sid in expired_students:
                await conn.execute(
                    "INSERT INTO student (id, tenant_id, name, status) "
                    "VALUES ($1, $2, $3, 'active')",
                    sid,
                    expired_tenant_id,
                    f"过期学生-{str(sid)[:4]}",
                )
    finally:
        await conn.close()

    return {
        "tenant_id": str(tenant_id),
        "admin_phone": admin_phone,
        "admin_pass": admin_pass,
        "teacher_phone": teacher_phone,
        "teacher_pass": teacher_pass,
        "platform_phone": platform_phone,
        "platform_pass": platform_pass,
        "expired_tenant_id": str(expired_tenant_id),
        "expired_name": expired_name,
        "course_id": str(course_id),
        "course_name": course_name,
        "class_id": str(class_id),
        "class_name": class_name,
        "fb_class_name": fb_class_name,
        "fb_student_names": ["冒烟反馈学生1", "冒烟反馈学生2"],
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


def _pick_option(page: Page, text: str) -> None:
    """在已展开的 el-select 下拉里点选一项（按可见文本）。"""

    page.locator(".el-select-dropdown__item").filter(has_text=text).first.click()


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
            banner2 = admin.locator(".layout__banner")
            banner2.wait_for(state="visible", timeout=8000)
            check(
                "场景5: 续费(改回未来)后横幅常显，文案「距到期还有 N 天」",
                "距到期还有 30 天" in banner2.inner_text(),
            )
            check(
                "场景5: 续费后横幅为次要色(info)不制造焦虑",
                "el-alert--info" in (banner2.get_attribute("class") or ""),
            )

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

            # ---------------------------------------------------------------- 场景 7：7.4 平台后台
            platform_ctx = browser.new_context()
            platform = platform_ctx.new_page()
            track(platform)

            platform.goto(f"{FRONTEND}/login")
            platform.get_by_placeholder("请输入手机号", exact=True).fill(creds["platform_phone"])
            platform.get_by_placeholder("请输入密码", exact=True).fill(creds["platform_pass"])
            platform.get_by_role("button", name="登录").click()
            platform.wait_for_url("**/platform/tenants", timeout=15000)
            check("场景7: platform_admin 登录后落点 /platform/tenants", True)

            expect(platform.locator(".stats__card")).to_have_count(3)
            check("场景7: 三张概览卡可见", True)

            # 搜索唯一机构名：历史累积的过期租户会把目标挤到第二页，先过滤再定位
            platform.get_by_placeholder("搜索机构名称", exact=True).fill(creds["expired_name"])
            platform.get_by_role("button", name="搜索").click()

            expired_row = platform.locator("tr").filter(has_text=creds["expired_name"])
            expired_row.first.wait_for(state="visible", timeout=8000)
            check("场景7: 列表出现过期机构行", True)
            expect(expired_row.first.locator(".el-tag").get_by_text("已到期")).to_have_count(1)
            check("场景7: 过期机构 tag=已到期（红）", True)

            # 详情抽屉：用量数字与改库值一致（seed 2 学生）
            expired_row.first.get_by_role("button", name="详情").click()
            drawer = platform.locator(".el-drawer:visible", has_text="租户详情")
            drawer.wait_for(state="visible", timeout=8000)
            drawer.get_by_text("订阅信息").wait_for(state="visible", timeout=8000)
            check("场景7: 详情抽屉订阅信息可见", True)
            drawer.get_by_text("管理员联系方式").wait_for(state="visible", timeout=8000)
            check("场景7: 详情抽屉管理员联系方式可见", True)
            drawer.get_by_text("在档学生 2 / 100").wait_for(state="visible", timeout=8000)
            check("场景7: 详情用量数字与改库值一致（2/100）", True)

            # 续费 30 天 → tag 由红变绿
            drawer.get_by_role("button", name="续费").click()
            renew_dlg = platform.locator(".el-dialog:visible", has_text="续费")
            renew_dlg.wait_for(state="visible", timeout=8000)
            expect(renew_dlg.get_by_role("button", name="90 天")).to_have_count(1)
            check("场景7: 续费弹窗快捷天数按钮可见", True)
            renew_dlg.get_by_role("button", name="30 天").click()
            renew_dlg.get_by_role("button", name="确认续费").click()
            platform.get_by_text("续费成功").wait_for(state="visible", timeout=8000)
            check("场景7: 续费 30 天成功", True)

            expired_row2 = platform.locator("tr").filter(has_text=creds["expired_name"])
            expect(expired_row2.first.locator(".el-tag").get_by_text("正常")).to_have_count(1)
            check("场景7: 续费后 tag 由「已到期」变「正常」（绿）", True)

            platform_ctx.close()

            # 机构 admin 直输平台后台 → 403
            admin2_ctx = browser.new_context()
            admin2 = admin2_ctx.new_page()
            track(admin2)
            _login(admin2, creds["admin_phone"], creds["admin_pass"])
            admin2.goto(f"{FRONTEND}/platform/tenants")
            admin2.get_by_text("403 · 无权访问").wait_for(state="visible", timeout=10000)
            check("场景7: 机构 admin 直输 /platform/tenants 被拦 403", "/403" in admin2.url)
            admin2_ctx.close()

            # ---------------------------------------------------- 场景 8：047 课时账本 + 退费强确认
            admin3_ctx = browser.new_context()
            admin3 = admin3_ctx.new_page()
            track(admin3)
            _login(admin3, creds["admin_phone"], creds["admin_pass"])

            # 新建账本学生（经 UI，避免与 7.3 限额场景的学生数撞车）
            ledger_student_name = f"冒烟账本学生-{uuid4().hex[:6]}"
            admin3.goto(f"{FRONTEND}/archive/students")
            admin3.get_by_role("button", name="新建学生").click()
            sdlg = admin3.locator(".el-dialog:visible", has_text="新建学生")
            sdlg.wait_for(state="visible", timeout=8000)
            sdlg.get_by_placeholder("必填", exact=True).fill(ledger_student_name)
            sdlg.get_by_role("button", name="保存").click()
            admin3.get_by_text("已新建").wait_for(state="visible", timeout=8000)

            # 学生列表「账本」入口跳转
            srow = admin3.locator("tr").filter(has_text=ledger_student_name)
            srow.first.wait_for(state="visible", timeout=8000)
            srow.first.get_by_role("button", name="账本").click()
            admin3.wait_for_url("**/ledger", timeout=15000)
            check("场景8: 学生列表「账本」入口跳转账本页", "/ledger" in admin3.url)

            balance_num = admin3.locator(".ledger__balance-num")
            balance_num.wait_for(state="visible", timeout=8000)
            check("场景8: 初始余额为 0", balance_num.inner_text().strip() == "0")

            # 购课登记 → 余额 0→10
            admin3.get_by_role("button", name="购课登记").click()
            pdialog = admin3.locator(".el-dialog:visible", has_text="购课登记")
            pdialog.wait_for(state="visible", timeout=8000)
            pdialog.locator(".el-select__wrapper").first.click()
            _pick_option(admin3, creds["course_name"])
            pdialog.get_by_role("button", name="确认购课").click()
            admin3.get_by_text("已登记购课").wait_for(state="visible", timeout=8000)
            expect(balance_num).to_have_text("10", timeout=8000)
            check("场景8: 购课登记后余额 0→10", True)
            check(
                "场景8: 课时包「10次卡」出现",
                "10次卡" in admin3.locator(".ledger__package-name").first.inner_text(),
            )

            # 退费：第一步表单 → 第二步红色强确认
            admin3.get_by_role("button", name="退费").click()
            rdialog = admin3.locator(".el-dialog:visible", has_text="退费")
            rdialog.wait_for(state="visible", timeout=8000)
            rdialog.locator(".el-select__wrapper").first.click()
            _pick_option(admin3, "10次卡")
            rdialog.get_by_placeholder("退费原因必填").fill("冒烟退费验证")
            rdialog.get_by_role("button", name="下一步：确认退费").click()

            cdialog = admin3.locator(".el-dialog:visible", has_text="退费确认")
            cdialog.wait_for(state="visible", timeout=8000)
            confirm_text = cdialog.inner_text()
            check("场景8: 强确认显示学生姓名", ledger_student_name in confirm_text)
            check("场景8: 强确认显示课时包名", "10次卡" in confirm_text)
            nums = cdialog.locator(".refund-confirm__num")
            check("场景8: 强确认显示退课时数 1", nums.nth(0).inner_text().strip() == "1")
            check("场景8: 强确认显示退费金额 ¥0", nums.nth(1).inner_text().strip() == "¥0")
            check("场景8: 强确认按钮文案为「确认退费」", "确认退费" in confirm_text)
            cdialog.get_by_role("button", name="确认退费").click()
            admin3.get_by_text("已退费").wait_for(state="visible", timeout=8000)

            expect(balance_num).to_have_text("9", timeout=8000)
            check("场景8: 退费后余额 10→9", True)
            admin3.get_by_role("cell", name="退费").first.wait_for(state="visible", timeout=8000)
            check("场景8: 流水表出现「退费」类型行", True)

            # ---------------------------------------------------------------- 场景 9：047 班级结课
            admin3.goto(f"{FRONTEND}/classes")
            crow = admin3.locator("tr").filter(has_text=creds["class_name"])
            crow.first.wait_for(state="visible", timeout=8000)
            crow.first.get_by_role("button", name="结课").click()
            mbox = admin3.locator(".el-message-box")
            mbox.wait_for(state="visible", timeout=8000)
            check("场景9: 结课二次确认弹窗出现", "结课确认" in mbox.inner_text())
            mbox.get_by_role("button", name="确认结课").click()
            crow2 = admin3.locator("tr").filter(has_text=creds["class_name"])
            expect(crow2.first.locator(".el-tag").get_by_text("已结课")).to_have_count(1)
            check("场景9: 结课后状态 tag 变「已结课」（灰）", True)
            check(
                "场景9: 结课后出现「恢复」入口",
                crow2.first.get_by_role("button", name="恢复").count() == 1,
            )

            # ---------------------------------------------------- 场景 10：048 课后反馈
            admin3.goto(f"{FRONTEND}/scheduling/timetable")
            fb_block = admin3.locator(".timetable__block").filter(has_text=creds["fb_class_name"])
            fb_block.first.wait_for(state="visible", timeout=10000)
            fb_block.first.click()

            fb_dialog = admin3.locator(".el-dialog:visible", has_text="课次详情")
            fb_dialog.wait_for(state="visible", timeout=8000)
            check("场景10: 课次详情弹窗出现「课后反馈」区块", "课后反馈" in fb_dialog.inner_text())

            fb_rows = fb_dialog.locator(".feedback__row")
            expect(fb_rows).to_have_count(2, timeout=8000)
            check("场景10: 反馈列表显示班级 2 名学员", True)

            # 学生 1 首写：填写 → 保存 → 「已反馈」tag
            fb_name1 = creds["fb_student_names"][0]
            row1 = fb_dialog.locator(".feedback__row").filter(has_text=fb_name1)
            row1.get_by_role("button", name="填写").click()
            edit_dialog = admin3.locator(".el-dialog:visible", has_text="填写课后反馈")
            edit_dialog.wait_for(state="visible", timeout=8000)
            check("场景10: 填写反馈弹窗出现", True)
            edit_dialog.locator("textarea").fill(
                "今天讲了方程式的解法，小明表现很积极，主动上台解题"
            )
            edit_dialog.get_by_role("button", name="保存").click()
            admin3.get_by_text("已保存反馈").wait_for(state="visible", timeout=8000)
            check("场景10: 保存反馈成功提示", True)
            expect(row1.locator(".el-tag").get_by_text("已反馈")).to_have_count(1, timeout=8000)
            check("场景10: 保存后学生行出现「已反馈」tag", True)

            # 重进详情 → 内容正确显示 + 可编辑
            fb_dialog.get_by_role("button", name="关闭").click()
            admin3.locator(".timetable__block").filter(
                has_text=creds["fb_class_name"]
            ).first.click()
            fb_dialog2 = admin3.locator(".el-dialog:visible", has_text="课次详情")
            fb_dialog2.wait_for(state="visible", timeout=8000)
            row1_re = fb_dialog2.locator(".feedback__row").filter(has_text=fb_name1)
            expect(row1_re.locator(".el-tag").get_by_text("已反馈")).to_have_count(1, timeout=8000)
            check("场景10: 重进详情内容正确显示", "今天讲了方程式的解法" in row1_re.inner_text())

            row1_re.get_by_role("button", name="编辑").click()
            edit_dialog2 = admin3.locator(".el-dialog:visible", has_text="填写课后反馈")
            edit_dialog2.wait_for(state="visible", timeout=8000)
            check(
                "场景10: 编辑弹窗回显已有反馈",
                "今天讲了方程式的解法" in edit_dialog2.locator("textarea").input_value(),
            )
            edit_dialog2.locator("textarea").fill(
                "今天讲了方程式与几何，小明已经完全掌握，进步很大"
            )
            edit_dialog2.get_by_role("button", name="保存").click()
            admin3.get_by_text("已保存反馈").wait_for(state="visible", timeout=8000)
            expect(row1_re.get_by_text("今天讲了方程式与几何")).to_have_count(1, timeout=8000)
            check("场景10: 编辑后内容更新为最新", True)

            admin3_ctx.close()
        finally:
            browser.close()


def main() -> None:
    creds = asyncio.run(seed())
    print(
        f"[seed] tenant={creds['tenant_id']} "
        f"admin={creds['admin_phone']} teacher={creds['teacher_phone']} "
        f"platform={creds['platform_phone']} expired={creds['expired_name']}"
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
