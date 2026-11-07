# 学生信息管理系统（多租户 SaaS）

面向培训/托管机构的多租户 SaaS 学生信息管理系统：管理端 Web + 家长端微信小程序。覆盖学生/家长/老师档案、课程班级与课时包、只增流水的课时账本、规律排课与冲突校验、签到扣课时（同事务+幂等）、退费/转班/补课、经营报表、家长端课表余额查询与课堂反馈、微信消息触达降级链（订阅消息→服务号→短信），以及机构自助开通的 SaaS 运营能力。

**一期已收官（048 步）**。现状事实源 = `docs/phase0/08-implementation-delta.md` §0 最新块；设计文档 01~07 是基线，实现与基线的偏离、加表必改的测试数字、已锁定的编码约定，全部记在那份「交接单」里。

> **动手前必读**：`docs/phase0/08-implementation-delta.md`。表数现状以它为准（当前 **29 张** / 业务层 22 张 / RLS 业务 22 + `subscribe_auth`）；Alembic head = `0016_lesson_feedback`。

## 技术栈

- Python 3.12 / FastAPI / Pydantic Settings
- PostgreSQL 16 / SQLAlchemy 2（asyncpg）/ Alembic
- Ruff / pytest / GitHub Actions
- Docker 多阶段构建，运行用户为非 root
- 管理端 Vue 3 + Element Plus + Vite；家长端原生微信小程序（零依赖、零构建链）

## 本地启动（三步）

1. 复制环境变量
   ```bash
   cp .env.example .env
   ```
   Windows PowerShell 可用 `Copy-Item .env.example .env`。

2. 启动数据库与 API
   ```bash
   docker compose up --build
   ```

3. 验证服务
   ```bash
   curl http://localhost:8000/health
   # {"status":"ok"}

   curl http://localhost:8000/health/db
   # {"status":"ok","database":"ok"}
   ```

`GET /health` 只检查进程存活；`GET /health/db` 执行 `SELECT 1`，数据库不可用时返回 **503**。API 文档在 <http://localhost:8000/docs>。

> 默认映射 5432 与 8000 端口；若本机端口被占用，请修改 `docker-compose.yml` 的宿主机端口。`.env` 含本地开发凭据，已被 git 忽略，禁止提交真实密码。

## 直接运行（不使用 API 容器）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
docker compose up -d db
alembic upgrade head
uvicorn app.main:app --reload
```

Windows 没有 Docker 时，也可以让 `DATABASE_URL` 指向本机 PostgreSQL 16，再运行 `uvicorn`。

## 数据库迁移

```bash
alembic upgrade head     # 建 29 张表（含索引、唯一约束、RLS 策略）
alembic downgrade base   # 回滚到空库
alembic current          # 当前版本
```

初始迁移 `alembic/versions/0001_initial_schema.py` 与 `docs/phase0/04-data-dictionary.md` 一一对应：平台层 4 张、微信层 3 张、业务层 17 张。后续迁移（文件名以 `alembic/versions/` 实际为准）：

| 迁移 | 一句话摘要 |
|------|-----------|
| `0002_rls_app_role` | 创建非 superuser、无 `BYPASSRLS` 的 `app_user` 并授予业务表 DML |
| `0003_auth_identity` | `staff_account.password_hash` + 三个认证专用 `SECURITY DEFINER` 查询函数 |
| `0004_provisioning_grants` | 平台层授权 + 租户名索引 |
| `0005_invite_codes` | 第 25 张表 `invite_code`（家长自助绑定凭证）+ RLS + `auth_invite_code` |
| `0006_course_class_fields` | 课程模板字段（时长/备注/状态）+ 班级学期、状态列 |
| `0007_purchase_course_ledger_index` | `purchase` 加 `course_id` + 扣减选包/按包求余额索引 |
| `0008_tenant_holiday` | 第 26 张表 `tenant_holiday`（机构节假日，排课避让） |
| `0009_lesson_exclude` | `lesson` 加 EXCLUDE 约束（同教室/同老师时间重叠，边界相接不算） |
| `0010_org_config_audit` | `org_config` 加 `updated_by`/`updated_at`（考勤扣课规则读写） |
| `0011_subscribe_auth_quota` | `subscribe_auth` 加 `available_count` + `updated_at`（订阅额度） |
| `0012_in_app_message` | 第 27 张表 `in_app_message`（站内信/红点，签到成功后同事务写入） |
| `0013_notification` | 第 28 张表 `notification`（通知中心三态状态机 + 幂等键） |
| `0014_org_config_sms_daily_budget` | `org_config` 加 `sms_daily_budget`（短信日预算） |
| `0015_org_config_scheduler` | `org_config` 加 `lesson_reminder_hours`/`balance_alert_threshold`，短信预算默认 50→200 |
| `0016_lesson_feedback` | 第 29 张表 `lesson_feedback`（课堂反馈，唯一键 `(lesson_id, student_id)`） |

> **租户隔离**：业务层 22 张表 + `subscribe_auth` 共 23 张启用 `ENABLE`/`FORCE ROW LEVEL SECURITY`。运行时 `DATABASE_URL` 必须使用 `app_user`；`MIGRATION_DATABASE_URL` 仅供 Alembic 执行 DDL。每个租户事务以 `SELECT set_config('app.tenant_id', :tenant_id, true)` 设置上下文，事务结束自动清除，连接归还连接池时 rollback，不残留到下一个请求。

## 认证与权限

- `POST /auth/login`：员工手机号 + 密码，argon2id 校验，签发 access/refresh JWT（HS256，默认 2h/30d）。
- `POST /auth/refresh`：refresh token 换新 access token；过期返回 401 `TOKEN_EXPIRED`。
- `POST /auth/wechat-login`：家长微信登录，`jscode2session` 封装为可替换客户端；local/test 默认桩模式，无需真实 appid。
- `POST /me/active-student`：切换孩子前实时复核绑定集合，重签含目标 `tenant_id` 的 JWT；`GET /me/students` 返回本人跨机构绑定孩子。
- `POST /platform/tenants`（平台管理员）：单事务开通租户——建 `tenant`、默认套餐 `tenant_subscription`、`org_config`（`absence_deduct_rule=none`）与首个管理员账号；同名机构 409 `TENANT_NAME_EXISTS`，靠 `pg_advisory_xact_lock` 保证幂等、不留半初始化数据。
- 档案模块（1.6）：`/students` `/parents` `/teachers` `/classrooms` 的 CRUD（删=状态位）、`/bindings` 教务直绑、`/invite-codes` 一次性邀请码、`GET /classes` 只读；老师侧自动按任教班级收窄。
- 课程班级（2.1/2.2）：`/courses` CRUD（被班级引用后只能停用，删除返回 409 `COURSE_IN_USE`）、`/classes` CRUD（创建必填课程/老师/教室/学期起止，容量缺省继承教室且不得超过）、`/classes/{id}/students` 招收与退出（结课、非在读、满员、重复入班均拒绝，招收时锁班级行防并发超额）；老师对班级与名单同样自动收窄，非任教返回 404。
- 课时包与缴费（2.3）：`POST /purchases` 内联课时包定义（课时数/赠课/有效期/实收），同一事务写 `purchase` + `purchase` 流水 + 独立 `grant` 赠课流水，包定义固化成快照；`GET /students/{id}/packages` 实时给出各包余额与状态（有效/已过期/已耗尽）。
- 课时账本（2.4）：`GET /students/{id}/ledger` 流水分页、`GET /students/{id}/balance` 实时 `SUM(delta)`、`POST /ledger/adjustments` 必填原因且 delta ≠ 0；扣减服务按「先过期先扣 → 同日先购先扣」选包，`SELECT … FOR UPDATE` 锁学生名下有效包，单包不足拆多条 consume，余额不足整体回滚。
- 退费/转班/补课（2.5）：`POST /refunds` 写一条 refund 负流水挂原包（剩余部分不隐式清零，包行加锁防并发打穿）；`POST /transfers` 同事务换 roster、**不产生流水**，跨课程班级直接 422（要跨课程先退费再购买）；`POST /lessons/{id}/make-ups` 补课 grant +1 流水（`lesson_id`=缺勤课次即防重键，同一缺勤只补一次），并把学生加进目标课次 attendance 名单（status=makeup 占位）；补课排期 `POST /makeup-lessons`（独立顶层前缀）。
- 课堂反馈（048）：`PUT /lessons/{id}/feedback` 批量 upsert（任课老师本人 + 教务 + admin，写别人任教课次 404）、`GET /lessons/{id}/feedback` 管理端查、`GET /me/children/{id}/feedback` 家长端倒序分页；反馈**不发任何通知**。
- 家长自助绑定（1.6）：`POST /me/bindings` 凭邀请码在**同一事务**内消耗邀请码、建 `parent`、写 `parent_student` 并重签 JWT；解绑后下一次请求即失效（权限不做缓存）。

登录发生在租户已知之前，而 `staff_account` 受 RLS 保护。系统不放宽 RLS、也不把 owner 连接交给 API，而是通过三个最小权限 `SECURITY DEFINER` 函数只按手机号/微信身份返回认证所需列；验证通过后所有业务查询仍走 `app_user` + 租户上下文。同一手机号在多个机构且密码相同时无法安全判定租户，登录按失败处理（后续可用机构码补强）。邀请码查码同理走 `auth_invite_code`，因为家长兑换时还没有租户上下文。

JWT 内的角色和租户在每次请求都会回库复核：签名有效但角色被篡改、租户被替换、账号停用或孩子解绑，一律 401/403。

## 测试与检查

静态检查：

```bash
ruff check .
ruff format --check .
```

### 本地跑真库测试

真实数据库相关的测试（迁移 up→down→up、RLS、通知/账本等）需要可连接的 PostgreSQL 并设 `RUN_DB_TESTS=1`：

```bash
APP_ENV=test RUN_DB_TESTS=1 python -m pytest -q
```

`APP_ENV=test` 让引擎用 `NullPool`（每用例一个事件循环，连接池会复用已关闭循环的 asyncpg 连接）。049 起 `tests/conftest.py` 顶部已 `os.environ.setdefault("APP_ENV", "test")` 兜底，即便 `.env` 写的是 `local`，测试进程也会跑在 test 引擎配置下——两种写法都能稳定全过（全量 **425 项**）。

小程序单测（零依赖，Node 内置 test runner）：

```bash
node --test miniprogram/tests/*.test.js   # 87 项
```

GitHub Actions 在每次 push / pull request 执行 **五 job**：lint（ruff check + format --check）、test（pytest，自带 PostgreSQL 16 服务容器）、web（vue-tsc + vite build）、docker-smoke（真实构建并验证 `/health` 与 `/health/db`）、miniprogram（`node --test`）。

测试分层（带真库时共 425 项）：

| 分组 | 文件 | 覆盖 |
|------|------|------|
| 结构自检 | `test_models` / `test_migrations` / `test_rls_coverage` | 表数锚定（平台 4 / 微信 3 / 业务 22 / 全库 29）、迁移 up→down→up、RLS 审计 |
| 租户隔离 | `test_tenant_isolation` | 双租户 RLS、运行时角色不可绕过、连接复用不残留上下文 |
| 认证权限 | `test_auth_login` / `test_permissions` / `test_permission_matrix` | 登录/刷新/微信登录、角色矩阵、越权三道闸门 |
| 平台开通 | `test_tenant_provisioning` / `test_platform_admin` / `test_platform_register` | 开通租户、租户列表/续费、自助注册、同名 409 |
| 档案 | `test_archive` | 档案 CRUD、老师收窄、邀请码绑定与过期、跨租户隔离 |
| 课程班级 | `test_course_classes` | 课程被引用禁删、教室/班级容量边界、结课与在读校验 |
| 排课 | `test_schedule_rule` / `test_tenant_holiday` / `test_lesson_generation` / `test_lesson_ops` / `test_conflict` | 规律排课、节假日、课次生成、调课取消、冲突（含并发） |
| 签到 | `test_attendance` | 签到扣课、幂等、makeup 转正、缺勤扣课规则 |
| 账本交易 | `test_ledger` / `test_reconcile` | 账实一致、多包顺序、10 并发不超扣、对账零差异 |
| 课堂反馈 | `test_feedback` | 老师写→家长查、覆盖更新、跨课次 404、幂等 |
| 套餐限额 | `test_subscription` / `test_plan_limits` | 到期降级、学生数/消息量限额 |
| 通知 | `test_notification_center` / `test_notification_events` / `test_fallback_chain` / `test_wechat_subscribe` / `test_admin_*` / `test_me_notifications` | 三态状态机、订阅/服务号/短信降级链、日预算 |
| 定时任务 | `test_scheduler` | 上课提醒、余额预警、夜间静默 |
| 员工账号 | `test_staff_admin` | 角色分级、停用/注销/复号 |
| 家长端 | `test_me_portal` / `test_me_subscribe` | 课表/余额/明细/反馈、订阅授权 |
| 报表 | `test_reports` | 出勤率/课耗/老师课时 |
| 健康 | `test_health` | `/health`、`/health/db` |
| 阶段验收 | `test_phase1_acceptance` / `test_phase2_acceptance` | 攻击面回归、混合流水验收 |

本地演示数据（仅 `APP_ENV=local`，可重复执行）：

```bash
python -m scripts.seed_demo     # 演示机构 13000000001 / 平台运营 13000000000，密码 demo123456
```

课时账本对账（任意环境可跑，零差异退出码 0）：

```bash
python -m scripts.reconcile_ledger          # 人类可读
python -m scripts.reconcile_ledger --json   # 机器可读
```

校验项（2.5 扩展后）：包余额 ↔ `SUM(delta)`、学生总额 ↔ 包之和、行形状（consume 必挂包+课次，其余必挂包）、**任何包余额不得为负**、每课次 consume 总量 ↔ 该课次 `attendance.deduct_lessons` 之和（阶段 3 签到接入前此条降级为 warning，只提示不计数）。warnings 单独计数、不影响退出码；**手动触发即可**，每日定时调度属部署阶段事项。

## 目录结构

```text
.
├── app/
│   ├── main.py              # 应用工厂、请求日志中间件、lifespan
│   ├── api/
│   │   ├── router.py        # 路由聚合
│   │   ├── deps.py          # 共享依赖（DB 会话、身份、租户会话）
│   │   ├── errors.py        # 统一错误信封 + trace_id
│   │   ├── permissions.py   # require_role / 家长与老师数据边界
│   │   └── routes/
│   │       ├── health.py    # /health、/health/db
│   │       ├── auth.py      # /auth/login、/auth/wechat-login、/auth/refresh
│   │       ├── me.py        # /me 组：学生、切换孩子、绑定、课表/余额/明细/反馈
│   │       ├── staff.py     # /staff 员工账号、/me/account 注销
│   │       ├── admin.py     # /admin 通知管理/统计/短信用量
│   │       ├── platform.py  # /platform 租户开通/列表/续费/自助注册
│   │       ├── archive.py   # 档案、绑定、邀请码
│   │       ├── course.py    # 课程、班级、班级学员
│   │       ├── schedule.py  # 排课规律、节假日
│   │       ├── lesson.py    # 课次生成/单个课次/调课取消/补课/反馈
│   │       ├── attendance.py# 签到
│   │       ├── ledger.py    # 缴费、课时包、流水、余额、调整、退费转班
│   │       ├── report.py    # 出勤率/课耗/老师课时
│   │       ├── holiday.py   # 节假日
│   │       ├── trade.py     # 交易（退费/转班/补课）
│   │       └── org_config.py# 机构配置
│   ├── core/
│   │   ├── auth.py          # Principal 身份契约
│   │   ├── config.py        # pydantic-settings
│   │   ├── errors.py        # AppError
│   │   ├── logging.py       # JSON 结构化日志
│   │   └── security.py      # argon2 密码哈希 + JWT 签发/校验
│   ├── db/
│   │   ├── base.py          # SQLAlchemy DeclarativeBase
│   │   ├── engine.py        # async engine（APP_ENV=test 用 NullPool）
│   │   ├── session.py       # AsyncSession 依赖
│   │   └── tenant.py        # 事务级租户上下文
│   ├── models/              # 29 张表：platform/wechat/archive/course/trade/ledger/schedule/attendance/org/notification/feedback
│   │   ├── mixins.py        # UUID 主键、created_at、tenant_id 公共列
│   ├── schemas/             # Pydantic 模型（auth/me/archive/course/ledger/...）
│   └── services/            # 业务服务（auth/ledger/attendance/trade/notification/feedback/...）
├── web/                     # 管理端 Vue 3 + Element Plus + Vite
├── miniprogram/             # 家长端原生小程序（零依赖、零构建链）
├── alembic/                 # 迁移环境（versions/0001…0016）
├── scripts/                 # seed_demo（本地演示）、reconcile_ledger（账本对账）等
├── tests/                   # 真库测试（RUN_DB_TESTS=1 时 425 项）
├── docs/phase0/             # 阶段 0 设计文档 + 实现增量台账
├── docs/deploy.md           # 部署文档（含反代前缀清单）
├── docs/miniprogram-launch-checklist.md  # 小程序上线检查清单（049）
├── deploy/                  # 生产 compose、Nginx 反代、env.prod.example
├── .github/workflows/ci.yml # lint + pytest + web + docker-smoke + miniprogram
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## 设计文档

- `docs/phase0/01-user-stories.md` — 0.1 功能拆解 · 用户故事 · MVP 边界
- `docs/phase0/02-permission-matrix.md` — 0.2 角色权限矩阵（含越权防线）
- `docs/phase0/03-tenant-isolation.md` — 0.3 多租户隔离技术决策（ADR）
- `docs/phase0/04-er-diagram.md` — 0.4 全量 ER 图（Mermaid）
- `docs/phase0/04-data-dictionary.md` — 0.4 字段字典
- `docs/phase0/05-api-spec.md` — 0.5 API 清单（一期全量含权限与租户过滤）
- `docs/phase0/06-wechat-architecture.md` — 0.6 微信端架构（统一小程序 + unionid 打通）
- `docs/phase0/07-notification-fallback.md` — 0.7 消息降级链（时序图 + 状态机）
- `docs/phase0/08-implementation-delta.md` — 0.8 实现增量台账 / 交接单（现状事实源）
