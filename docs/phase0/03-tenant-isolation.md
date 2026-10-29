# 0.3 多租户隔离技术决策（ADR）

> 对应《学生信息管理系统_分步执行清单》步骤 0.3。
> 技术栈：FastAPI + PostgreSQL + SQLAlchemy + Alembic。
> 上游依据：`02-permission-matrix.md` 三道闸门（租户/角色/数据归属）与 V-01~V-08 越权防线。
> 本文把「租户闸门」落成可实施的技术决策，六项决策每项给出 结论 + 理由 + 落地方式，无待定项。

## 决策 1：隔离模型选型 —— 共享库共享表 + tenant_id + PG RLS

**结论**：采用 **共享库、共享表（shared-schema）**，每张业务表带 `tenant_id`，由 PostgreSQL 行级安全（RLS）兜底。弃用 schema-per-tenant 与 db-per-tenant。

**理由**：
- 目标客群是大量中小托管/培训机构，单租户数据量小、租户数量会持续增长。共享表运维成本最低、迁移最简单（Alembic 一套迁移跑全量）。
- schema-per-tenant：每租户一套表，Alembic 迁移要按租户数扇出，几百租户时迁移与连接管理失控；跨租户概览（决策 6）要 UNION 全部 schema，几乎不可维护。
- db-per-tenant：隔离最强，但连接池、备份、迁移成本随租户线性增长，对一期「先跑通生意线」是过度设计；且平台侧跨租户统计同样困难。
- 共享表的唯一软肋是「忘加租户过滤就串数据」，这正是 RLS 要兜底的事——把隔离从「靠程序员记得」变成「靠数据库强制」。

**落地方式**：
- 业务表统一含 `tenant_id UUID NOT NULL`，建 `(tenant_id, …)` 复合索引。
- `tenant_id` 由服务端从登录态解析（决策 3），不接受客户端传参。
- 范围对齐 0.1 的 11 个模块（档案/课程班级/课时账本/排课/签到/交易/报表/家长端/消息/机构配置/平台运营）中的全部业务表。

## 决策 2：RLS 策略 —— 全业务表启用 + FORCE RLS，应用角色非 owner

**结论**：所有业务表 `ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY`，策略用 `USING`（读/改/删可见性）与 `WITH CHECK`（写入归属）双约束；应用连接角色**不得是表 owner、不得带 BYPASSRLS**。

**理由**：
- 表 owner 默认绕过 RLS；`FORCE RLS` 让 owner 也受策略约束，杜绝「用 owner 连接就全见」的后门。
- `USING` 管「能看哪些行」，`WITH CHECK` 管「能写哪些行」，缺一会出现「能写入他租户」的漏洞。
- 应用角色与 owner 分离 + 无 BYPASSRLS，是「跨租户必空集」这条红线在数据库层的最终保证。

**落地方式**（策略 SQL 示例，业务表逐一执行，Alembic 迁移固化）：

```sql
-- 以 student 表为例，其余 10 模块业务表同构套用
ALTER TABLE student ENABLE ROW LEVEL SECURITY;
ALTER TABLE student FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_select ON student
  FOR SELECT USING (tenant_id = current_setting('app.tenant_id', true)::uuid);

CREATE POLICY tenant_isolation_modify ON student
  FOR ALL
  USING      (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

-- 应用连接角色：独立建号，非 owner、无 BYPASSRLS
-- CREATE ROLE app_user LOGIN PASSWORD '...' NOSUPERUSER NOBYPASSRLS;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON student TO app_user;
```

- 迁移规范：新建任何业务表必须在同一迁移里附带 RLS enable/force + 两条策略，CI 做「无 RLS 业务表」检查。
- `current_setting('app.tenant_id', true)` 第二参数 `true` 表示未设置时返回 NULL 而非报错；`tenant_id = NULL` 不成立，故**未设置租户上下文时策略按空集处理**（读不到任何行），这是预期行为，防止漏 SET 时全表暴露。

## 决策 3：租户上下文中间件 —— FastAPI 依赖注入 + SET app.tenant_id

**结论**：用 FastAPI 依赖注入在请求入口解析租户，`tenant_id` 从 JWT（服务端校验签名后）解析；每条 SQL 执行前 `SET LOCAL app.tenant_id`；**拒绝客户端以任何形式传入 tenant_id**（呼应 V-02 伪造 token、V-04 改 tenant_id）。

**理由**：
- 把租户来源收敛到「唯一可信入口（JWT）」，杜绝 query/body/header 里的 `tenant_id` 被篡改。
- `SET LOCAL` 只在当前事务内有效，事务结束自动失效，避免上下文残留到下一个请求。
- 依赖注入让每个路由天然拿到租户上下文，业务代码不必手工传 tenant_id，减少漏加过滤的人为失误。

**落地方式**（骨架，非最终实现）：

```python
# deps.py
from fastapi import Depends, HTTPException
from jose import jwt

def get_tenant_id(token: str = Depends(oauth2_scheme)) -> str:
    payload = jwt.decode(token, SECRET, algorithms=[ALG])   # 服务端验签
    tid = payload.get("tenant_id")
    if not tid:
        raise HTTPException(401, "no tenant")
    return tid  # 只认 JWT，拒绝请求里的 tenant_id 参数

async def tenant_session(tid: str = Depends(get_tenant_id),
                         db=Depends(get_db)):
    # 每个请求一个事务，事务内 SET LOCAL，提交/回滚后自动失效
    await db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tid})
    yield db
```

- 所有业务路由统一 `Depends(tenant_session)`；平台管理员走独立通道（决策 6），不经过此 SET。

## 决策 4：连接池 reset —— SQLAlchemy pool reset 事件清理上下文

**结论**：用 **SQLAlchemy 连接池 `reset_on_return` / `PoolEvents.reset` 事件**，在连接归还池前清理租户上下文（`RESET app.tenant_id` 或 `DISCARD ALL`），**不靠连接串参数**。

**理由**：
- 连接池复用是串租户的高发点：A 请求 `SET` 了 tenant_id，连接没清就还给池，B 请求复用同一连接会带着 A 的租户上下文跑。
- `SET LOCAL`（决策 3）在事务内已自动失效，是主防线；池 reset 是第二道兜底，双保险覆盖「非事务 SET / 连接被异常复用」的边角。
- 连接串参数（如 startup 参数）只在建连时设一次，无法应对「同一连接服务多租户」的复用场景，弃用。

**落地方式**：

```python
from sqlalchemy import event
from sqlalchemy.engine import Engine

@event.listens_for(Engine, "reset")   # 连接归还池时触发
def _reset_tenant(dbapi_conn, connection_record):
    cur = dbapi_conn.cursor()
    cur.execute("RESET app.tenant_id")  # 清掉可能残留的租户上下文
    cur.close()
```

- 配合决策 3 的 `SET LOCAL`：正常情况下 reset 是空操作，异常路径下兜底；集成测试里加「同连接连续两租户」用例验证无残留。

## 决策 5：跨租户测试方法 —— 双租户集成测试 + 四条固定攻击用例

**结论**：建双租户（A/B）集成测试基线：A 造数据 → B 查询必须空集、B 写入必须被拒；并把 0.2 的 V-01~V-04 四条攻击固化为 pytest 用例。

**理由**：
- 红线一「跨租户必空集」必须有自动化测试锁死，否则后续重构一次就可能破窗。
- 四条攻击对应最常见的四类越权，固定用例让安全回归成为 CI 的一部分，不靠人肉 review。

**落地方式**（pytest 用例骨架：用例名 + 断言，不写实现）：

```python
# tests/tenant/test_isolation.py  （骨架）

def test_tenant_b_cannot_read_tenant_a_data():
    # A 租户造 student/ledger/schedule 数据
    # 以 B 租户身份查询同模块列表/详情
    # assert 结果为空集（len == 0），详情接口 404/403
    ...

def test_tenant_b_cannot_write_into_tenant_a():
    # B 租户尝试向 A 租户对象写入/修改
    # assert WITH CHECK 触发：写入被拒绝或 tenant_id 被强制改写为 B
    ...

def test_v01_idor_cross_parent_by_param():
    # 家长 A 改 studentId 参数访问家长 B 的孩子
    # assert 403 / 空集，绑定关系校验拦截
    ...

def test_v02_forged_role_token_rejected():
    # 伪造/篡改 token 角色字段冒充教务/管理员
    # assert 401/403，服务端角色校验拦截
    ...

def test_v03_id_enumeration_blocked():
    # 遍历学生/课次 ID 批量拉取
    # assert 仅返回归属内的数据，越界 ID 一律 404/403
    ...

def test_v04_client_tenant_id_ignored():
    # 请求里显式传 tenant_id=B，但登录态是 A
    # assert 服务端强制用登录态 A，B 的数据不可见
    ...

def test_pool_reset_no_tenant_leak():
    # 同一连接先服务 A 再服务 B
    # assert B 的查询不含 A 的数据（连接 reset 生效）
    ...
```

## 决策 6：平台管理员跨租户概览 —— 独立只读角色 + BYPASSRLS 边界控制

**结论**：平台管理员走**独立只读角色**，只对「概览/聚合接口」授权，业务明细接口一律 403；其跨租户能力用**受限 BYPASSRLS（或独立聚合视图）**实现，但作用域被严格限定在聚合数据上（呼应 V-07 平台下钻）。

**理由**：
- 平台需要「多少租户、各租户用量/健康度」，必须跨租户；但绝不能看到学生/账本等明细。
- 若直接给平台角色全库 BYPASSRLS，一旦该角色被滥用即全盘泄露。把 BYPASSRLS 限定在「只读 + 仅聚合视图/接口」，明细表对平台角色不可达，从权限上封死下钻。

**落地方式**：
- 建独立 DB 角色 `platform_ro`：`NOSUPERUSER`，只授「聚合视图/物化视图」的 SELECT，不授任何业务表明细权限。
- 跨租户概览用**聚合视图**（按 tenant 分组的计数/用量），由迁移脚本预先建好；`platform_ro` 只对视图 `BYPASSRLS` 或通过视图定义者权限读取，业务明细表保持 FORCE RLS 且无授权。
- 应用侧平台管理员账号绑定 `platform_ro`，路由层再校验：命中业务明细接口直接 403。
- 合规审计（US-405）只读元数据/脱敏结果，同样走聚合视图，不开放明细。

---

## 与 0.2 的呼应对照

| 0.2 防线 | 本文落点 |
|----------|----------|
| 租户闸门 / V-04 跨租户 | 决策 1（模型）、决策 2（RLS+FORCE）、决策 3（拒客户端 tenant_id） |
| V-02 伪造 token | 决策 3（JWT 服务端验签、角色不信任自声明） |
| V-01 IDOR / V-03 猜 ID | 决策 5 固定攻击用例 + 数据归属（0.2 数据归属闸门） |
| V-07 平台下钻看明细 | 决策 6（独立只读角色 + 聚合视图边界） |
| 串租户（连接池复用） | 决策 4（pool reset 兜底） |

> 自检：RLS 策略、连接池 reset、跨租户测试方法三项均到可执行粒度；六项决策每项有结论有理由、无待定；与 0.2 三道闸门及 V-01~V-08 一一呼应。
