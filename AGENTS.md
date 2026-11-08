# AGENTS.md — 教培机构管理系统 · 换机交接文档

> 新 AI 窗口必读。读完本文 + `docs/phase0/08-implementation-delta.md`（§0 顶部最新块）即可接手，不要重新调研。

## 一、项目是什么

- **多租户 SaaS 教培机构管理系统**：排课、签到扣课、课时账本、购课/退费/转班/补课、家长微信小程序、消息触达（订阅消息→服务号→短信→站内信四级降级链）、订阅计费与到期降级、平台后台。
- **给谁用**：先在用户本人运营的机构自用跑通，再开放其他机构自助注册（商业化产品）。用户职业与项目定位无关，不要拿职业推断需求。
- **解决什么问题**：原来用 Excel + 手动微信通知管排课扣课，错扣漏扣、家长拿不到余额提醒。
- **现状：一期已完成（指令 020~049 全部验收通过），可以直接上线**。CI 五 job 全绿，测试 425+ 全过，Playwright 前端断言 42+ 全过。
- 仓库：`github.com/HBUTFAKER/new-student-`，分支 `main`。

## 二、怎么跑起来

- **技术栈**：后端 Python 3.12 + FastAPI + SQLAlchemy 2 (asyncpg) + PostgreSQL 16 + Alembic；管理端 Vue 3 + Element Plus + Vite + TypeScript；家长端原生微信小程序（零依赖零构建链）。
- **依赖安装**：后端 `pip install -e .[dev]`（见 pyproject.toml）；前端 `cd web && npm install`；小程序无依赖。
- **启动**：
  ```bash
  cp .env.example .env          # 环境变量模板
  docker compose up -d db       # PostgreSQL 16
  alembic upgrade head          # 建 29 张表（含 RLS 策略）
  uvicorn app.main:app --reload # 后端，端口 8000
  cd web && npm run dev         # 管理端，Vite 默认端口 5173
  ```
  本机验证标准：`curl http://localhost:8000/health/db` 返回 200（返回 503 = 数据库不通）；API 文档在 `http://localhost:8000/docs`。
- **测试**：`pytest`（后端 425+）；`cd web && npm run build`（含 vue-tsc 类型检查）；Playwright 前端冒烟脚本在仓库内（交接单 §4 有索引）。
- **环境变量**：全部抄 `.env.example`，必填 `DATABASE_URL`。微信/短信通道一期全是 stub，真实密钥不进仓库。CI 用 GitHub Secrets；本地验证 CI 结果用 `git credential fill` 取 token 调 GitHub API（本机没有 gh CLI）。
- **数据库迁移**：目录 `alembic/versions/`，最新到 `0016_lesson_feedback`。RLS 覆盖审计是 CI 强制项，新增表必须同步挂 RLS，漏了 CI 故意挂红。
- **初始化数据**：生产首个平台管理员 `python scripts/init_platform_admin.py`（密码从 env 读，幂等）；对账 `scripts/reconcile_ledger.py`（支持 `--tenant` 过滤）。

## 三、目录地图

- `app/models/` — SQLAlchemy 模型（29 张表，platform.py 是平台层：租户/订阅/用量）
- `app/api/routes/` — 路由：`platform.py`（自助注册/续费/用量）、`staff.py`（员工账号管理）、`trade.py`（购课/退费/转班/补课）、`ledger.py`（账本）、`course.py`（班级）、`lesson.py`（课次/反馈）、`schedule.py`（排课）、`attendance.py`（签到扣课）
- `app/services/` — 业务核心：`ledger.py`（扣课：先过期先扣+拆包+FOR UPDATE，禁重写）、`notification_center.py`（6.1 消息引擎状态机）、`scheduler.py`（三循环定时任务）、`subscription.py`（到期闸门/限额）、`tenant_provisioning.py`（开租户）
- `app/services/channels/` — 通知通道实现（微信订阅消息/短信，均 stub，原子扣额度逻辑是真的）
- `web/src/views/` — 管理端页面（`platform/` 是平台后台；`archive/` 学生归档）；设计令牌在 `web/src/styles/`，所有颜色走令牌，裸色值零容忍
- `miniprogram/` — 家长小程序（pages：home/balance/schedule/feedback/ledger/messages/bind/login/switch-student/notify）
- `docs/phase0/` — 设计文档：01 用户故事、02 权限矩阵（99 行，044 已逐行落测试）、03 租户隔离、05 API 规范、**08 交接单（§0 是最新状态，必读）**
- `deploy/` — nginx.conf（生产反代，新 API 前缀必须同步加白名单）、`docs/deploy.md` 从零到上线运维文档、`docs/miniprogram-launch-checklist.md` 小程序上线清单
- `tests/` — `test_permission_matrix.py`（矩阵逐行）、`test_rls_coverage.py`（22 业务表锚定）、`test_staff_admin.py`、账本并发测试（真双连接+SAVEPOINT）
- `scripts/` — init_platform_admin / reconcile_ledger / seed_demo_full（未提交的演示脚本）

## 四、当前进度

- **已完成**：阶段 1~7 全部（多租户地基、排课冲突、签到幂等扣课、管理端、家长小程序、消息降级链、定时任务、自助注册、员工账号管理、到期只读降级、套餐限额、平台后台、越权对账测试、部署包、前端美化、账本/交易域界面、课堂反馈）。指令编号 020~049 全部验收通过。
- **做到一半的**：无。
- **已知问题**：无未修复 bug。未提交文件 `acceptance-demo/`、`048-课堂反馈-验收演示.html`、`scripts/seed_demo_full.py` 是验收演示残留，可删可不管。
- **下一步计划**：真上线（需用户提供小程序 appid + 服务器域名 + VPS，照 `docs/miniprogram-launch-checklist.md` 和 `docs/deploy.md` 走）；二期候选：反馈推送通知、退费审批流、服务号真实授权、前端主包分包优化、课包模板、租户停用。

## 五、设计决策（为什么 + 否掉了什么）

- **租户隔离三道闸**：应用层强制 tenant_id 过滤 + PostgreSQL RLS 双保险 + 权限矩阵逐行测试；跨租户必须 404 不许 403（403 泄露"资源存在"）。
- **扣课只允许先过期先扣**：家长买的课时包有有效期，先扣快过期的；`ledger.py` 的拆包逻辑禁重写，改前必读。
- **消息四级降级链**：订阅消息→服务号→短信→站内信，第一个成功即停；`dedupe_key` 防重发，部分成功不重发；余额预警按扣课流水 id 去重（每扣一课可再提醒，不是每充值周期一次）。
- **到期只读降级而非锁死登录**（用户拍板）：写请求统一被闸门中间件拦 402，数据永久保留；续费从 max(今天, 原到期日) 起算；到期当天还能用。
- **试过但否掉的方案**：
  - CORS 放后端 → 否，Vite 同源代理 / 生产 Nginx 反代。
  - 退费审批流 → 否（用户拍板：教务直退 + 前端强确认弹窗，审批挂二期）。
  - 归档学生腾名额 → 否（用户拍板：归档照占名额）。
  - 课堂反馈发推送 → 否（一期家长自己点开看，不发推送不烧额度；推送挂二期）。
  - 服务号真实授权一期做 → 否（没有认证服务号跑不了真的，stub 挂二期）。
  - CI 加 Playwright job → 否，Playwright 只做本地冒烟（Windows 环境 CI 跑不稳定）。
  - 按路由白名单逐个放行写闸门 → 否，默认拦写 + 白名单豁免方向反了会漏。

## 六、地雷区（动了会出事）

- `app/services/ledger.py` 扣课核心（FOR UPDATE + 拆包）：并发重写会造成双重扣课，**这是全项目最大红线**。
- `app/services/notification_center.py` 状态机：`dedupe_key` 有 ON CONFLICT 唯一约束兜底，改语义会导致消息重发或丢失。
- `tests/` 里的并发/幂等测试：真双连接 + SAVEPOINT，不许为了过测试削弱断言。
- `alembic/versions/` 已发布的迁移：不许改已合并的文件，只能追加新版本。
- `deploy/nginx.conf` 前缀白名单：**新加 API 路由前缀必须同步更新**，漏了就是 049 修的那种"本地好使、线上静默 404"（`/makeup-lessons` 前科）。
- 权限矩阵 `docs/phase0/02-permission-matrix.md` 与 `tests/test_permission_matrix.py` 必须同步改，单改一边 CI 挂。
- RLS：任何新表漏挂 RLS 策略，`test_rls_coverage.py` 会挂——这是故意的，别去改测试迁就代码。
- `.env` / 密钥 / 生产 compose 里的密码：不进仓库、不改默认值。

## 七、用户习惯（从 020~049 合作中总结）

- **称呼**：叫他"瑞哥"或"兄弟"；自称随意但别端着。
- **说话偏好**：回复要短，结论先行，两三句说完；他字多了不看。
- **最烦**：委婉绕话、让人会错意的"高情商"表达；长篇大论；编造没验证的结论。
- **决策习惯**：功能/产品取舍必须先问他拍板（AskUserQuestion 或表格列选项）；**拍板事项必须用加粗+表格放在回答最后两三段**，夹在长文中间会被漏掉。
- **商业化定位（必读）**：这些项目是奔着商业化去的，目标是**卖出去**，不是自用玩具。评估方案时：
  1. 带商业化视角：客户是谁、愿不愿意付钱、同行怎么做的
  2. 设计优先降低客户上手成本，花架子往后放
  3. 数据要能导出，客户想走也能带走自己的数据，这本身就是卖点
  4. 报价、授权、试用这类决策别替他拍板，给方案和代价让他自己定
  5. 但仍然反对为了"像大厂"堆架构，没有真实需求别上微服务
- **需求类型**：高频提"前端要好看"、"这个功能会不会让家长烦"、"涉及钱和名额的规则"；他讲完立场后按他的立场执行，不要当"网上的说法"转述。
- **工程流程**：多 AI 分工——他当信使在"发令部"（出指令）和"施工队"（写代码）窗口间转交；每步开新窗口省上下文；指令文件命名 `0XX-instruction.md` 放工作区根目录。
- **每条施工指令末尾必须写死**：完成后 commit + push，CI 全绿才算完。
- 验收流程：git log → CI check-runs → 交接单 §0 → 代码抽查（重点抽金额/并发/越权相关 diff）→ 更新项目记忆。

## 八、红线（原样抄，不许改）

- 不擅自 git push 到主分支
- 不删数据库表、不批量改数据
- 不动线上配置和密钥
- 不编造没验证过的结论，不吹成果
- 不用"首先/其次/总之/希望对你有帮助/作为一个AI"
- 回复要短，结论先行，别写小作文

## 九、开工须知

1. **先读**（按序）：`AGENTS.md`（本文件）→ `docs/phase0/08-implementation-delta.md` 的 §0 顶部块和 §4 → `README.md`。设计细节按需读 `docs/phase0/` 对应文档，不要一次全读。
2. **验证环境**：
   ```bash
   git pull && git log --oneline -5     # 确认在 main 最新
   cp .env.example .env                 # 没有的话
   docker compose up -d db && alembic upgrade head
   pytest -q                            # 后端
   ```
3. **跑通标准**：`curl localhost:8000/health/db` 返回 200；pytest 全绿（425+，若少跑说明 conftest 的 APP_ENV 没生效，049 修过这个坑）；CI 五 job（lint/test/web/docker-smoke/miniprogram）全绿——查 CI 用 `git credential fill` 取 token 调 GitHub API `/commits/<sha>/check-runs`。
4. 拿不准的事标【待确认】问用户，别猜；改代码前先看第六节地雷区。
