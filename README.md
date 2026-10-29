# 学生信息管理系统（多租户 SaaS）

面向培训/托管机构的多租户 SaaS 学生信息管理系统：管理端 Web + 家长端微信小程序。覆盖学生/家长/老师档案、课程班级与课时包、只增流水的课时账本、规律排课与冲突校验、签到扣课时（同事务+幂等）、退费/转班/补课、经营报表、家长端课表余额查询与课堂反馈、微信消息触达降级链（订阅消息→服务号→短信），以及机构自助开通的 SaaS 运营能力。

当前处于 **阶段 1（工程脚手架）**。阶段 0 的七份设计文档已完成；1.2 已按 0.4 字段字典落地 **24 张表**与初始 Alembic 迁移，业务接口从 1.3 起逐步实现。

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
uvicorn app.main:app --reload
```

Windows 没有 Docker 时，也可以让 `DATABASE_URL` 指向本机 PostgreSQL 16，再运行 `uvicorn`。

## 数据库迁移

```bash
alembic upgrade head     # 建 24 张表（含索引、唯一约束、RLS 策略）
alembic downgrade base   # 回滚到空库
alembic current          # 当前版本
```

初始迁移 `alembic/versions/0001_initial_schema.py` 与 `docs/phase0/04-data-dictionary.md` 一一对应：平台层 4 张、微信层 3 张、业务层 17 张。

> **租户隔离现状**：迁移已为 18 张租户数据表启用 `ENABLE`/`FORCE ROW LEVEL SECURITY` 与读写两条策略，但当前连接仍使用建库账号（表属主），RLS 实际不生效，租户过滤由应用层 `tenant_id` 条件保证。按 0.3 决策 2，后续步骤需要引入独立的非 owner 应用角色。

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

## 目录结构

```text
.
├── app/
│   ├── main.py              # 应用工厂、请求日志中间件、lifespan
│   ├── api/
│   │   ├── router.py        # 路由聚合
│   │   ├── deps.py          # 共享依赖（DB 会话）
│   │   └── routes/
│   │       └── health.py    # /health、/health/db
│   ├── core/
│   │   ├── config.py        # pydantic-settings
│   │   ├── logging.py       # JSON 结构化日志
│   │   └── security.py      # 认证占位（1.2 起实现）
│   ├── db/
│   │   ├── base.py          # SQLAlchemy DeclarativeBase
│   │   ├── engine.py        # async engine
│   │   └── session.py       # AsyncSession 依赖
│   ├── models/              # 24 张表：platform/wechat/archive/course/trade/ledger/schedule/attendance/org
│   │   ├── mixins.py        # UUID 主键、created_at、tenant_id 公共列
│   ├── schemas/             # Pydantic 模型
│   └── services/            # 业务服务
├── alembic/                 # 迁移环境（versions/0001_initial_schema.py）
├── tests/                   # 模型自检 + 健康检查 + 迁移测试
├── docs/phase0/             # 阶段 0 设计文档
├── .github/workflows/ci.yml # lint + pytest
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## 设计文档

- `docs/phase0/01-user-stories.md` — 0.1 功能拆解 · 用户故事 · MVP 边界
- `docs/phase0/02-permission-matrix.md` — 0.2 角色权限矩阵（含越权防线）
- `docs/phase0/03-tenant-isolation.md` — 0.3 多租户隔离技术决策（ADR）
- `docs/phase0/04-er-diagram.md` — 0.4 全量 ER 图（Mermaid，三层 24 表）
- `docs/phase0/04-data-dictionary.md` — 0.4 字段字典
- `docs/phase0/05-api-spec.md` — 0.5 API 清单（一期全量含权限与租户过滤）
- `docs/phase0/06-wechat-architecture.md` — 0.6 微信端架构（统一小程序 + unionid 打通）
- `docs/phase0/07-notification-fallback.md` — 0.7 消息降级链（时序图 + 状态机）
