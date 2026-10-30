# 0.8 实现增量台账 / 开发 ↔ 发令部交接单

> **这是两个窗口之间的固定通道。**
> 发令部（出指令）只读 §0；开发（我）每完成一步，在 §0 顶部插一块新的。
> 瑞哥只需要说一句「X.X 改完了」，发令部打开本文件第 20 行就能接上，不需要他转述。
>
> **读法**
> - 发令部：读 §0 最新一块 + §4 已锁定约定，再出指令。别只看 01~07 原文，那是基线，不是现状。
> - 开发：动表结构、动既有测试、动已上线接口前，先读 §1 与 §2。

当前状态：**阶段 3 收官（3.5 完成）**，Alembic head = `0010_org_config_audit`，全库 26 张表（业务层 19 张）。阶段 4 已开工：**4.1 管理端前端骨架完成**（`web/`，零后端改动、零迁移）。

---

## §0 交接区（最新在最上面）

### [4.1] 2026-10-30 17:45 · commit `605a218`（已推送，CI 41034979954 全绿）

**我改了什么**

- 新增 `web/`：Vite 6 + Vue 3.5 + TS 5.9 + Element Plus 2.14 + Vue Router 4 + Pinia 3，pnpm 12（`packageManager` 锁版本，`pnpm-lock.yaml` 已提交）。**`app/`、`alembic/`、`tests/`、`scripts/` 一行未动，零迁移**。
- 登录页（唯一业务页）：`POST /auth/login`，手机号 + 密码，401 `AUTH_FAILED` 用后端原文内联提示（不弹层）。
- token：`access_token` 内存 + localStorage 双存，`refresh_token` 同存，角色与账号存 `wb.profile`；刷新页面登录态不丢。
- `web/src/api/http.ts`：自动 `Authorization: Bearer`；401 用 refresh 续期并**重放原请求**，并发 401 走**单航班**（一次 refresh）；refresh 也失效 → 清 token + 回登录页并带 `?redirect=`；错误统一按 05 §0 `{error:{code,message}}` 解析，`ElMessage` 提示（登录接口用 `skipErrorToast` 交给页面内联）。
- 布局：左菜单 + 顶栏（角色标签 / 账号 / 退出）+ 主内容区。菜单按角色过滤：admin/registrar 7 项（工作台·档案·班级·排课·签到·报表·机构设置），teacher 4 项（工作台·我的班级·我的课表·签到）。
- 路由守卫：未登录 → `/login?redirect=<原地址>`；登录后回原目标；已登录但角色不在 `meta.roles` → 拦到 `/403`，**目标组件不渲染**；已登录访问 `/login` → 回工作台；未知路由 404。
- 占位页：`/archive` `/classes` `/scheduling` `/attendance` `/reports` `/settings` `/my-classes` `/my-schedule` 全部指向同一个 `PlaceholderView`，路由名与路径已固定。
- CI 新增 `web` job（Node 20 + pnpm install --frozen-lockfile + `vue-tsc --noEmit` + `vite build`）；后端 `lint` / `test` / `docker-smoke` 三个 job 未动。
- 验证：`pnpm build`（含类型检查）通过；Playwright + 本机 Edge 跑 25 项行为断言全过（见下）。

**必须跟着改的地方**

1. **pnpm 12 默认不跑依赖的构建脚本**：`web/pnpm-workspace.yaml` 里有 `allowBuilds: esbuild`。删了它 `pnpm install` 直接 `ERR_PNPM_IGNORED_BUILDS`（CI 也红）。后续加任何带 postinstall 的依赖，都要在这里追加放行，否则 CI 挂。
2. **`pnpm-workspace.yaml` 是 pnpm 12 的配置落点**（`package.json` 里的 `pnpm` 字段已不再被读取）。别把它删成空文件，它是真配置。
3. **后端没有员工 profile 接口**：05 §1.1 只返回 `role` / `tenant_id`，所以顶栏"当前用户"显示的是**登录手机号 + 角色中文名**（admin→机构管理员 / registrar→教务 / teacher→老师）。若发令部要显示真实姓名，得先加 `GET /me/profile`（本步禁止新增接口，没做）。
4. **菜单口径**：admin 与 registrar **菜单完全相同（7 项）**。0.2 矩阵里两者的差别在"员工账号分配 / 敏感操作留痕"，本步没有对应页面。若 4.x 要区分，请在新指令里明确列菜单，不要默认沿用。
5. **登录成功跳转**：带 `?redirect=` 时回原页，否则一律 `/dashboard`（三种角色共用工作台）。**role 目前不改变首页**，指令里"按 role 跳转首页"按此口径落地。
6. **`?redirect=` 只接受站内相对路径**（`/` 开头且非 `//`），否则回落 `/dashboard`；防开放重定向。
7. **`VITE_API_BASE_URL`**：`.env.development` = `http://localhost:8000`；`.env.production` **留空 = 同源**，部署时需 Nginx 反代 `/auth`、`/students` 等路径，这条要在部署步骤确认，别到时候 404。

**给发令部的指令**

- 4.2 起加页面：在 `web/src/views/` 建组件 + 在 `web/src/router/index.ts` 注册路由，**权限只写在 `meta.roles`**，守卫已统一收口，不要在页面里另写角色判断。
- 所有请求走 `web/src/api/http.ts` 的 `http` 实例，**禁止 new axios**（否则没有 Bearer、没有 401 续期、没有统一报错解析）。
- 角色中文名集中在 `web/src/types/auth.ts` 的 `ROLE_LABELS`；新增角色要同步改这里、`STAFF_ROLES`、`router/index.ts` 的 roles 常量。
- 菜单只改 `web/src/config/menu.ts`，不要在布局组件里散写。
- 前端锁 pnpm 12（`packageManager`）；CI 的 `pnpm/action-setup` 会读它，**不要在 workflow 里另写 pnpm version**，否则版本冲突。
- 本步**未做按钮级权限**（菜单级 + 路由级已收口）。4.2 起页面内"新建/编辑/删除"按钮是否按角色隐藏，请在新指令里定口径。
- 已知限制（如实上报）：Element Plus 全量引入，主 chunk 1.27 MB（gzip 414 kB），构建有 chunk 体积警告。本步禁止引入额外依赖，没做按需引入；要优化请在后续步骤单开一条。

### [3.5] 2026-10-30 16:20 · commit `a22d03c`（已推送，CI 41023535871 全绿：lint/test/docker-smoke）

**我改了什么**

- 点名签到 `POST /lessons/{lesson_id}/attendance`（200）：请求 `{records:[{student_id,status}]}` → 响应 `{lesson_id, results:[{student_id,status,deduct_lessons,deduplicated,reason}]}`（05 §6.1 契约）。`cancelled` → 422 `LESSON_CANCELLED`；`now < start_at` → 422 `LESSON_NOT_STARTED`；教务/管理员任意课次，老师仅任教班级课次（按 `Class.teacher_id` 实时判定，否则 403 `OUT_OF_SCOPE`）。
- 三条红线落在 `app/services/attendance.py`：整批一个事务 + **每个学生一层 `begin_nested()` SAVEPOINT**；**幂等由唯一键裁决**（先 INSERT，命中 23505 再回读，不做先查后插）；扣课只调 `deduct_lessons`（`app/services/ledger.py` 一行没改，也没有任何 grant/refund/adjust 代码）。
- `present` 扣 1；`leave`/`absent` 按 `org_config.absence_deduct_rule`（`none`=0 / `deduct`=1）。
- 3.4 的 `makeup` 预登记行在正式签到时 **原地转正**：`SELECT ... FOR UPDATE` 后 UPDATE 为本次 status + 扣课；转正后即为最终状态，再提交走 `deduplicated`。
- 余额不足 → 该学生 `{status:"failed", reason:"BALANCE_EXHAUSTED"}`，savepoint 回滚（**不写 attendance 行**），其他人照常成功；购课后可单独重提，已成功的别人不重扣。
- `GET /lessons/{lesson_id}/attendance`（200，`{items,total}`，含 `student_id/status/deduct_lessons/operator_id`），权限同写入。
- `GET /org-config/absence-deduct-rule`：教务/管理员/老师可读；`PATCH` 仅 admin/registrar；非法值 422 `RULE_VALUE_INVALID`；留痕 `updated_by`/`updated_at`。
- 迁移 `0010_org_config_audit`：`org_config` 加 `updated_by`（uuid）、`updated_at`（timestamptz）两列。**不加表**，表数仍 26 / 业务层 19。
- 测试 12 项（`tests/test_attendance.py`，含验收线「签到 3 次扣 3 课时」）；全量 245 passed。

**必须跟着改的地方**

1. `tests/test_models.py::test_no_table_has_updated_at`：原断言「任何表都不许有 `updated_at`」与 024 要求的两列**直接冲突**。已把 `org_config` 作为唯一例外放行（并断言这两列确实存在），其余表继续禁。这不是回归，是指令与既有不变量的冲突，已按最小改动处理。
2. `scripts/reconcile_ledger.py`：修了一个**既有 bug**——孤儿包判定用的 `seen` 集合原本在每个学生循环里重置，同一机构第二个学生有课时包时，前一个学生的包会被误报 `orphan_purchase`。签到上线后一个机构必然多个学生各自买课，这个 bug 会让对账测试变红。已改为按租户累积。
3. 表数没动：26 / 业务层 19，`test_models`、`test_migrations` 的硬编码数字保持不变。

**给发令部的指令**

- **签到只产生 `consume`**（`none` 模式下不产生流水）；签到链路禁止写 grant/refund/adjust。
- **二维码自助签到按 01-user-stories 的二期清单后置二期**（本步只做点名签到）。
- `makeup` 行不是幂等重复：正式签到必须原地转正；转正后才是最终状态。
- 补课学生缺席按 `absence_deduct_rule` 同规则处理，不特殊化。
- `moved` 课次可签（时间已是新的），`cancelled` 不可签。
- 老师权限每次请求实时按 `Class.teacher_id` 判定，不做归属缓存。
- **已知限制（如实上报）**：签到每次每人只扣 1，所以「单包不足拆两条 `consume`」在签到入口**不可达**；拆包行为仍由 2.5 的 `deduct_lessons` 与 `tests/test_ledger.py::test_deduction_drains_earliest_expiry_first_and_splits_rows` 保证。本步测的是「先过期先扣」的顺序。

### [3.4] 2026-10-30 06:55 · commit `5af6cc5` + `51d44ad`（已推送，CI 40962744127 全绿）

**我改了什么**

- 单次加课 `POST /lessons`（201）：复用 3.3 的 `create_lesson`（引用+时间校验 → 三维度预检 → EXCLUDE 兜底 409 `SCHEDULE_CONFLICT`），补上了 `create_lesson` 原本缺的引用校验（班级/教室/老师存在、老师在职、班级未结课）与 aware/起止时间校验。
- 调课/取消 `PATCH /lessons/{id}`（200）：守门 `LESSON_ALREADY_DONE`（已有 `attendance` 行 **或** 已过 `start_at` → 422），调课与取消统一守门；调课改时间/教室/老师后 `status='moved'`，冲突走 `find_conflicts`（**排除自身**）→ 409 `LESSON_OVERLAP` 带 `conflicts` 明细，EXCLUDE 兜底；取消只翻 `status='cancelled'`（不删行，EXCLUDE 谓词自动忽略 → 时段释放）。
- 补课排期 `POST /makeup-lessons`（201，US-212）：在**目标课次**为该学员写一条 `attendance` 行，`status='makeup'`、`deduct_lessons=0`。校验：学员存在、缺勤课次上确有 `absent/leave` 记录（否则 422 `ABSENCE_NOT_FOUND`）、目标课次未来且 `normal`（422 `MAKEUP_TARGET_INVALID`）、同目标课次已占座 → 409 `MAKEUP_ALREADY_SCHEDULED`。跨班补课允许。
- 新增 `app/services/attendance.py`（`schedule_makeup`）；三条路由都收口 `OfficeUser`。
- 测试 12 项（`tests/test_lesson_ops.py`）；全量 233 passed。

**必须跟着改的地方**

1. **无**。本步没加表、没加列、没动既有测试数字（表数维持 26 / 业务层 19，`test_models`/`test_migrations` 不动）。
2. `create_lesson` 现在会做引用校验 + aware/起止校验——3.3 的并发测试直接调它，引用都是真实种子，不受影响。

**给发令部的指令**

- **调课/取消/补课一律不写 `lesson_ledger` 流水**（红线）。课时只随签到（3.5）变动。别让后续步骤在调课/取消/补课里写 grant/consume。
- 调课冲突用 `409 LESSON_OVERLAP`（05 明示）；单次加课沿用 `409 SCHEDULE_CONFLICT`（3.3 既有）。两个都带 `detail.conflicts`。
- **补课排期 ≠ 2.5 补课**：本步是纯排期（只写 attendance 占位行），2.5 的 `POST /lessons/{id}/make-ups` 是资金侧（写 `grant` +1 流水）。两者是两条独立接口，别混。
- **通知联动后置到 6.1**，本步不建通知表、不发通知、不建桩。留给 6.1 的事件清单：`lesson.moved` / `lesson.cancelled` / `makeup.scheduled`。
- **已知限制（如实上报，非遗漏）**：补课排期的「同一缺勤只排一次」以 attendance 唯一键 `(target_lesson_id, student_id)` 表达；`attendance` 没有「缺勤课次」回链列（缺勤→补课的链接由 2.5 的 `grant` 流水 `lesson_id` 承载），所以「同一缺勤排到**不同**目标课次」的跨目标去重不在本步能力内。若要严格跨目标去重，需在 `attendance` 加 `absent_lesson_id` 列（要走 §1 加列流程），或依赖 2.5 资金侧查重。发令部决定要不要补。
- 请求体 `reason` 可选，但 `attendance` 无 `reason` 列，接受后不落库（与退款 `refund_amount` 同口径）。

---

### [3.3] 2026-10-30 06:15 · commit `d96c79d` + `7449f43`（已推送，CI 40960432443 全绿）

**我改了什么**

- 冲突检测三层防线：service 预检（`app/services/conflict.py` 的 `find_conflicts`）+ PG EXCLUDE 约束（迁移 `0009_lesson_exclude`）+ 并发测试。
- 三维度：老师、教室、学员跨班（`class_enrollment` 子查询）。重叠判定 `start < other.end AND end > other.start`（边界相接不算），`status != cancelled` 不占位。
- `POST /lessons/generate` 响应**追加** `skipped_conflicts` + `conflicts`（只加字段，旧字段语义不变），撞冲突跳过不中断。
- 新增 `POST /schedule-rules/validate`：请求同 POST /schedule-rules，不落库，返回 `{ok, conflicts}`，OfficeUser。
- 新增 `create_lesson`（service 层：预检 + EXCLUDE 兜底 409，带 conflicts 明细；供并发测试与 3.4 单次加课复用，本步不暴露路由）。
- 错误信封支持 `detail`，409 `SCHEDULE_CONFLICT` 现在带 `conflicts` 数组。
- 测试 8 项；全量 221 passed。

**必须跟着改的地方**

1. `tests/test_ledger.py` 与 `tests/test_phase2_acceptance.py` 的 fixture 原本「同教室 + `now()`」插多条 lesson，EXCLUDE 上线后必撞约束 → 已改为各错开 4h 时间（无对时间值的断言，不影响账本逻辑）。
2. 迁移 0009 的存量重叠处理不是物理 DELETE（会撞 `lesson_ledger` 外键），改成 `UPDATE ... SET status='cancelled'`（保留 id 最小、软删）。
3. 本步**不加表**，表数维持 26 / 业务层 19，`test_models`/`test_migrations` 数字不动。

**给发令部的指令**

- `Lesson.status` 只有 `normal` / `moved` / `cancelled`（默认 normal，无枚举）；冲突检测与 EXCLUDE 谓词都排 `cancelled`。
- EXCLUDE 只加在 teacher、classroom 两维（`btree_gist` + `tstzrange`）；学员维度多对多做不了约束，靠预检 + 并发测试兜。
- 任何「插入单条课次」的新入口（3.4 的 `POST /lessons`）必须复用 `create_lesson`（预检 + 23P01→409），别再手写先查后插。
- 冲突明细结构 `{type, lesson_id, class_id, start_at, end_at}`，type 按 老师→教室→学员 聚合，时间 `+08:00`。

---

### [3.2] 2026-10-30 05:05 · commit `4a90d5e`（已推送，CI 40956405018 全绿）

**我改了什么**

- 新表 `tenant_holiday` + 迁移 `0008_tenant_holiday.py`（RLS 双策略 + `GRANT app_user`），没动任何已有表。
- 节假日三件套：`POST /tenant-holidays`（批量、重复日期计入 skipped）、`GET /tenant-holidays?year=`、`DELETE /tenant-holidays/{id}`。
- 课次批量生成：`POST /lessons/generate {class_id, weeks}` → `{generated, skipped_holidays, skipped_existing}`（200，幂等）。
- 课次查询：`GET /lessons?class_id=&from=&to=&page=`，时间统一 `+08:00`。
- 测试 8 项；全量 213 passed。

**必须跟着改的地方**

1. `tests/test_models.py` 的 `len(BUSINESS_TABLES) == 18` → **19**（已改）。
2. `tests/test_migrations.py` 的 `len(migrated["tables"]) == 25` → **26**（已改）。
   > 这两处是硬编码，**以后每加一张表都会再破一次**，改这两个数字是正常动作，不是回归。
3. `04-data-dictionary.md`：表数 25→26、业务层 18→19、章节标题 17→19、补 `tenant_holiday` 小节（已改）。
4. `04-er-diagram.md`：开头 25→26 / 18→19、自检表 17→19（已改；原文 17 与 18 本来就矛盾，一并统一）。
5. `05-api-spec.md` §5.1：加「实现现状」提示，指向本文件（已改）。

**给发令部的指令**

- 别再写「当前 25 张表 / 业务层 18 张」，实际 **26 / 19**。
- 别再要求 `POST /schedule-rules` 返回 `generated_lessons`，该契约已废，生成走 `POST /lessons/generate`。
- 涉及加表时，把 §1 的 6 步动作清单直接写进指令，否则会漏 RLS 或漏改测试数字。
- 3.3 待办范围见 §5。

---

### [3.1] 2026-10-30 04:20 · commit `0dd8267`（已推送，CI 40954533574 全绿）

**我改了什么**

- 排课规律 CRUD 五条：`POST/GET/GET{id}/PATCH/DELETE /schedule-rules`，落 `schedule_rule` 表，无迁移。
- 存储按 04 字典：**一行一个 weekday**，无 `weeks` 列。
- POST 支持 `weekdays`（一次写多天）与 `weeks`（只推导 `end_date`，不落库）；单个 `weekday`+`end_date` 的老写法兼容。
- 测试 6 项；全量 200 passed。

**必须跟着改的地方**

- 无。本步没动表、没动既有测试数字。

**给发令部的指令**

- `POST /schedule-rules` 的响应是 `{items, total}`，**不是** 05 原文的 `{id, generated_lessons}`。
- `weekday` 口径定为 **0 = 周一 … 6 = 周日**（`date.weekday()`），后续一律沿用。

---

### [1.6] · commit `c3aaf65`（补记，本文件尚未建立时完成）

**我改了什么**：`invite_code` 表（第 25 张 / 业务层第 18 张）+ 迁移 `0005`。

**必须跟着改的地方**：`test_models` 18、`test_migrations` 25 —— 当时已改。

**给发令部的指令**：表数以 04 字典当前值为准，不要用阶段 0 的 24 张。

---

## §1 硬约束：加表必改的清单（每加一张表都走一遍）

| 位置 | 断言 | 当前值 |
|------|------|:---:|
| `tests/test_models.py` | `len(BUSINESS_TABLES) == N`（含去重） | **19** |
| `tests/test_migrations.py` | `len(migrated["tables"]) == N` | **26** |

1. `app/models/<x>.py` 建模型，`tenant_id` 必带。
2. `app/models/__init__.py`：import + `__all__` + `BUSINESS_TABLES`。
3. `alembic/versions/000N_<x>.py`：建表 + `ENABLE`/`FORCE ROW LEVEL SECURITY` + 两条策略（select/modify）+ `GRANT ... TO app_user`，照 `0008_tenant_holiday.py` 抄。
4. 改上表两个数字。
5. 同步 `04-data-dictionary.md`（表清单标题、清单行、自检段、业务层章节标题）与 `04-er-diagram.md`（开头总数、业务层章节标题、自检表）。
6. 在 §0 顶部插一块交接说明。

> 漏第 3 步 → RLS 检查红；漏第 4 步 → 两条测试红；漏第 5 步 → 下一个人按错数字发指令。

---

## §2 接口契约变更（相对 05-api-spec）

### 2.1 `POST /schedule-rules` 不再顺带生成课次（3.1 + 3.2）

- 05 原文：201 `{ "id": "uuid", "generated_lessons": 26 }`，创建时按周生成课次。
- 实际：201 `{ "items": [...], "total": n }`，只写规律，**不碰 lesson 表**；生成走 2.2。
- 原因：3.1 已上线该契约不能回改；且录入与生成分两步更贴合实际业务。

### 2.2 新增：课次批量生成（3.2）

```
POST /lessons/generate
请求  { "class_id": "uuid", "weeks": 8 }          # weeks 1–52
响应 200 { "generated": 14, "skipped_holidays": 2, "skipped_existing": 16 }
```

- 窗口：`max(该班所有规律的 start_date, 今天)` 起 `weeks` 整周。
- 逐条约束：命中 `rule.weekday`，且日期在 `rule.start_date` ~ `rule.end_date` 内（`end_date` 可空）。
- 跳过 `tenant_holiday` 命中的整天 → `skipped_holidays`。
- 幂等：同班同时刻已存在 → `skipped_existing`，不报错，重复调用 `generated=0`。
- 200 而非 201：幂等操作。

### 2.3 新增：机构节假日（3.2）

```
POST   /tenant-holidays   { "dates": ["2026-11-05"], "name": "国庆节" }  → 200 {created, skipped}
GET    /tenant-holidays?year=2026                                        → {items, total}
DELETE /tenant-holidays/{id}                                             → 204
```

并发撞唯一键 → 409 `HOLIDAY_DUPLICATED`。

### 2.4 补齐 05 只列了一部分的 CRUD（3.1）

`GET /schedule-rules`（class_id/teacher_id/classroom_id/weekday + 分页，带三张名称）、`GET/PATCH/DELETE /schedule-rules/{id}`。

### 2.5 `GET /lessons` 参数收窄（3.2）

- 05 原文：`?class_id=&teacher_id=&from=&to=&page=`
- 实际：`?class_id=&from=&to=&page=`，**没有 `teacher_id`**。老师/家长按班级收窄留给课表接口（US-101）。

### 2.6 尚未实现（后置，不是遗漏）

| 项 | 出处 | 归属 |
|----|------|------|
| 二维码自助签到（`POST /lessons/{id}/qr-checkin`） | 05 二期清单 | 二期（发令部已裁决） |
| 通知中心（调课/取消/补课事件 `lesson.moved`/`lesson.cancelled`/`makeup.scheduled` 的联动） | 05 §6.1 | 6.1 |
| 老师/家长课表收窄（`GET /lessons` 按任教/绑定班级自动收窄） | 05 §5.3 | 课表接口（US-101） |

> 签到四条（点名/结果/规则读/规则写）已在 3.5 落地，从本表移除。

> 原 `POST /lessons`（单次加课）、`PATCH /lessons/{id}`（调课/取消）、`LESSON_OVERLAP` 独立错误码三条已在 3.4 落地，从本表移除。

---

## §3 数据字典变更（相对 04-data-dictionary）

| 表 | 变化 | 阶段 |
|----|------|------|
| `invite_code` | 新增（第 18 张业务表） | 1.6 |
| `tenant_holiday` | 新增（第 19 张业务表 / 全库第 26 张） | 3.2 |
| `org_config` | **加列**（不加表）：`updated_by` uuid、`updated_at` timestamptz，US-217 最小留痕 | 3.5 |

- 表数口径：平台层 4 + 微信层 3 + 业务层 19 = **26**。
- `tenant_holiday` 无 `updated_at`/`deleted_at`：节假日不软删，DELETE 物理删除后可重新登记。
- 模型属性名 `holiday_date`，列名仍是 `date`（避开 `datetime.date` 遮蔽）。
- `org_config` 是 **唯一** 带 `updated_at` 的表（US-217 留痕，迁移 `0010`）。`tests/test_models.py` 的「无表可有 updated_at」断言已把它列为唯一例外；其他表再加就红。完整审计日志（US-304）仍是后续阶段。

---

## §4 已锁定的约定（后续必须沿用，别另起一套）

| 约定 | 取值 | 出处 |
|------|------|------|
| `weekday` 口径 | **0 = 周一 … 6 = 周日**（`date.weekday()`） | 3.1 |
| 业务时区 | `BUSINESS_TZ` = UTC+8，定义在 `app/services/ledger.py` | 3.1 |
| 时间字段 | 一律 aware：`datetime.combine(day, t, tzinfo=BUSINESS_TZ)`；出参 `astimezone` 成 `+08:00`。naive 会炸 | 3.2 |
| 规律存储 | 一行一个 weekday，无 `weeks` 列；`weeks` 仅推导 `end_date = start + 7N - 1` | 3.1 |
| 权限收口 | 排课/节假日/课次全部 `OfficeUser`（admin/registrar）；老师、家长 403 `OUT_OF_SCOPE` | 0.2 矩阵 |
| 多租户 | `TenantDbSession` + RLS，`tenant_id` 由登录态注入；跨租户读改删一律 **404**（不是 403） | 0.3 |
| 错误码 | 字段非法 422 / 引用不存在 404 / 状态冲突 409 | — |
| 冲突重叠判定 | `start < other.end AND end > other.start`（边界相接不算）；`status != cancelled` 不占位 | 3.3 |
| 冲突 409 | `SCHEDULE_CONFLICT`，`detail={"conflicts": [...]}`；EXCLUDE 违例 23P01 → 409 | 3.3 |
| `Lesson.status` 取值 | `normal` / `moved` / `cancelled`（默认 normal，无枚举） | 3.3 |
| 签到幂等 | 唯一键 `(tenant_id, lesson_id, student_id)` 是裁判；命中最终状态 → `deduplicated`，命中 `makeup` → 原地转正 + 扣课 | 3.5 |
| 签到扣课 | `present` 扣 1；`leave`/`absent` 按 `org_config.absence_deduct_rule`；只调 `deduct_lessons`，禁止 grant/refund/adjust | 3.5 |
| 签到事务 | 整批一个事务 + 每学生一层 SAVEPOINT；余额不足只回滚该学生，不写 attendance 行 | 3.5 |

---

## §5 变更记录（按阶段）

| 阶段 | commit | 内容摘要 |
|------|--------|----------|
| 1.6 | `c3aaf65` | `invite_code` 追加（补记） |
| 3.1 | `0dd8267` | 排课规律 CRUD，`{items,total}` 契约；weekday 0–6 |
| 3.2 | `4a90d5e` | `tenant_holiday` + `0008`；`POST /lessons/generate`；节假日三件套；测试数字 18→19、25→26 |
| 文档 | `7e55f92` | 本文件建立；04/04-er/05 数字与契约提示修正 |
| 3.3 | `d96c79d` | `find_conflicts` 三维度 + 迁移 `0009` EXCLUDE + `create_lesson` + `validate` 接口；生成响应加 `skipped_conflicts` |
| 3.4 | `5af6cc5` | `POST /lessons`（复用 `create_lesson` 补引用校验）、`PATCH /lessons/{id}`（moved/cancelled，`LESSON_ALREADY_DONE` 守门 + `LESSON_OVERLAP`）、`POST /makeup-lessons`（纯排期写 attendance makeup 行，不碰流水） |
| 3.5 | `a22d03c` | 签到扣课（同事务 + 逐学生 SAVEPOINT + 唯一键幂等 + makeup 原地转正）；`GET attendance`；`org_config` 规则读写 + `updated_by/updated_at`（迁移 `0010`）；修对账脚本孤儿包误报 |
| 4.1 | `605a218` | `web/` 管理端骨架：登录、Pinia 登录态、Axios（Bearer + 401 单航班续期）、角色菜单、路由守卫（redirect 回跳 / 越权 403）、占位页；CI 加 `web` job。零后端改动 |
