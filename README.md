# 学生信息管理系统（多租户 SaaS）

面向培训/托管机构的多租户 SaaS 学生信息管理系统：管理端 Web + 家长端微信小程序。覆盖学生/家长/老师档案、课程班级与课时包、只增流水的课时账本、规律排课与冲突校验、签到扣课时（同事务+幂等）、退费/转班/补课、经营报表、家长端课表余额查询与课堂反馈、微信消息触达降级链（订阅消息→服务号→短信），以及机构自助开通的 SaaS 运营能力。

当前处于 **阶段 1（工程脚手架）**。阶段 0 的七份设计文档已完成；1.2 已按 0.4 字段字典落地 **24 张表**与初始 Alembic 迁移，1.3 已落地非 superuser 应用角色、真 RLS 隔离与事务级租户上下文。

## 技术栈

- Python 3.12 / FastAPI / Pydantic Settings
- PostgreSQL 16 / SQLAlchemy 2（asyncpg）/ Alembic
- Ruff / pytest / GitHub Actions
- Docker 多阶段构建，运行用户为非 root

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
alembic upgrade head     # 建 25 张表（含索引、唯一约束、RLS 策略）
alembic downgrade base   # 回滚到空库
alembic current          # 当前版本
```

初始迁移 `alembic/versions/0001_initial_schema.py` 与 `docs/phase0/04-data-dictionary.md` 一一对应：平台层 4 张、微信层 3 张、业务层 17 张。后续迁移：`0002_rls_app_role.py` 创建非 superuser、非 owner、无 `BYPASSRLS` 的 `app_user` 并授予业务表 DML；`0003_auth_identity.py` 增加 `staff_account.password_hash` 与三个认证专用 `SECURITY DEFINER` 查询函数；`0004_provisioning_grants.py` 补平台层授权与租户名索引；`0005_invite_codes.py` 新增第 25 张表 `invite_code`（家长自助绑定凭证）及其 RLS 策略和 `auth_invite_code` 查码函数；`0006_course_class_fields.py` 补课程模板字段（时长/备注/状态）与班级学期、状态列；`0007_purchase_course_ledger.py` 给 `purchase` 加 `course_id`，并补扣减选包与按包求余额的两个索引。

> **租户隔离**：18 张租户数据表均启用 `ENABLE`/`FORCE ROW LEVEL SECURITY`。运行时 `DATABASE_URL` 必须使用 `app_user`；`MIGRATION_DATABASE_URL` 仅供 Alembic 执行 DDL。每个租户事务以 `SELECT set_config('app.tenant_id', :tenant_id, true)` 设置上下文，事务结束自动清除，连接归还连接池时 rollback，不残留到下一个请求。

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
- 家长自助绑定（1.6）：`POST /me/bindings` 凭邀请码在**同一事务**内消耗邀请码、建 `parent`、写 `parent_student` 并重签 JWT；解绑后下一次请求即失效（权限不做缓存）。

登录发生在租户已知之前，而 `staff_account` 受 RLS 保护。系统不放宽 RLS、也不把 owner 连接交给 API，而是通过三个最小权限 `SECURITY DEFINER` 函数只按手机号/微信身份返回认证所需列；验证通过后所有业务查询仍走 `app_user` + 租户上下文。同一手机号在多个机构且密码相同时无法安全判定租户，登录按失败处理（后续可用机构码补强）。邀请码查码同理走 `auth_invite_code`，因为家长兑换时还没有租户上下文。

JWT 内的角色和租户在每次请求都会回库复核：签名有效但角色被篡改、租户被替换、账号停用或孩子解绑，一律 401/403。

## 测试与检查

```bash
ruff check .
ruff format --check .
pytest
```

真实数据库相关的测试（`/health/db`、迁移 upgrade→downgrade→upgrade）默认跳过；需要时设置 `RUN_DB_TESTS=1` 并提供可连接的 PostgreSQL：

```bash
RUN_DB_TESTS=1 pytest
```

GitHub Actions 在每次 push / pull request 时执行 Ruff 检查和 pytest，测试任务自带 PostgreSQL 16 服务容器；另有 Docker Compose 冒烟任务，真实构建并验证 `/health` 与 `/health/db`。

测试分层（带真库时共 189 项）：

| 文件 | 覆盖 |
|------|------|
| `tests/test_models.py` / `test_migrations.py` | 表数与租户列静态自检、迁移 upgrade→downgrade→upgrade、`compare_metadata` 零差异 |
| `tests/test_tenant_isolation.py` | 双租户 RLS、运行时角色不可绕过、连接复用不残留上下文 |
| `tests/test_auth_login.py` / `test_permissions.py` | 登录/刷新/微信登录、角色矩阵、伪造与换租户 token |
| `tests/test_tenant_provisioning.py` | 平台开通租户、同名 409、org_config 默认值、异常整体回滚 |
| `tests/test_archive.py` | 档案 CRUD、老师收窄、邀请码绑定与过期、跨租户隔离 |
| `tests/test_course_classes.py` | 课程被引用禁删、教室/班级容量边界、结课与在读校验、名单即时收窄、跨租户 |
| `tests/test_ledger.py` | 账实一致（20 赠 4 扣 8 = 16）、多包顺序与拆流水、同日先购先扣、过期包跳过、10 并发不超扣、adjust 原因/负余额、跨租户 404、对账脚本零差异 |
| `tests/test_phase1_acceptance.py` | 阶段 1 验收：两租户 + 改 URL 参数 / 伪造 token / 猜 ID 遍历三种攻击全部 403/404 或空集 |

本地演示数据（仅 `APP_ENV=local`，可重复执行）：

```bash
python -m scripts.seed_demo     # 演示机构 13000000001 / 平台运营 13000000000，密码 demo123456
```

课时账本对账（任意环境可跑，零差异退出码 0）：

```bash
python -m scripts.reconcile_ledger          # 人类可读
python -m scripts.reconcile_ledger --json   # 机器可读
```

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
│   │       ├── me.py        # /me/students、/me/active-student、/me/bindings
│   │       ├── platform.py  # /platform/tenants（平台开通）
│   │       ├── archive.py   # 档案、绑定、邀请码
│   │       ├── course.py    # 课程、班级、班级学员
│   │       └── ledger.py    # 缴费、课时包、流水、余额、调整
│   ├── core/
│   │   ├── auth.py          # Principal 身份契约
│   │   ├── config.py        # pydantic-settings
│   │   ├── errors.py        # AppError
│   │   ├── logging.py       # JSON 结构化日志
│   │   └── security.py      # argon2 密码哈希 + JWT 签发/校验
│   ├── db/
│   │   ├── base.py          # SQLAlchemy DeclarativeBase
│   │   ├── engine.py        # async engine
│   │   ├── session.py       # AsyncSession 依赖
│   │   └── tenant.py        # 事务级租户上下文
│   ├── models/              # 25 张表：platform/wechat/archive/course/trade/ledger/schedule/attendance/org
│   │   ├── mixins.py        # UUID 主键、created_at、tenant_id 公共列
│   ├── schemas/             # Pydantic 模型（auth/me/archive/course/ledger/platform）
│   └── services/            # auth / wechat / tenant_provisioning / bindings / ledger
├── alembic/                 # 迁移环境（versions/0001…0007）
├── scripts/                 # seed_demo（本地演示）、reconcile_ledger（账本对账）
├── tests/                   # 模型自检、迁移、RLS、认证、开通、档案、课程班级、账本、阶段验收
├── docs/phase0/             # 阶段 0 设计文档
├── .github/workflows/ci.yml # lint + pytest + docker-smoke
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## 设计文档

- `docs/phase0/01-user-stories.md` — 0.1 功能拆解 · 用户故事 · MVP 边界
- `docs/phase0/02-permission-matrix.md` — 0.2 角色权限矩阵（含越权防线）
- `docs/phase0/03-tenant-isolation.md` — 0.3 多租户隔离技术决策（ADR）
- `docs/phase0/04-er-diagram.md` — 0.4 全量 ER 图（Mermaid，三层 25 表）
- `docs/phase0/04-data-dictionary.md` — 0.4 字段字典
- `docs/phase0/05-api-spec.md` — 0.5 API 清单（一期全量含权限与租户过滤）
- `docs/phase0/06-wechat-architecture.md` — 0.6 微信端架构（统一小程序 + unionid 打通）
- `docs/phase0/07-notification-fallback.md` — 0.7 消息降级链（时序图 + 状态机）
