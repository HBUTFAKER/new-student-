# 0.8 实现增量台账 / 开发 ↔ 发令部交接单

> **这是两个窗口之间的固定通道。**
> 发令部（出指令）只读 §0；开发（我）每完成一步，在 §0 顶部插一块新的。
> 瑞哥只需要说一句「X.X 改完了」，发令部打开本文件第 20 行就能接上，不需要他转述。
>
> **读法**
> - 发令部：读 §0 最新一块 + §4 已锁定约定，再出指令。别只看 01~07 原文，那是基线，不是现状。
> - 开发：动表结构、动既有测试、动已上线接口前，先读 §1 与 §2。

当前状态：**阶段 3 收官（3.5 完成）**，Alembic head = `0010_org_config_audit`，全库 26 张表（业务层 19 张）。**阶段 4 收官**：4.1 前端骨架 + 4.2 档案与班级管理页 + 4.3 排课日历与拖拽调课 + 4.4 上（三个只读报表接口）+ 4.4 下（报表前端页）全部完成。4.4 上是阶段 4 唯一动后端的部分：零迁移、零新表。**阶段 5 进行中**：5.1 家长端小程序骨架 + 登录 + 请求封装、5.2 邀请码绑定 + 多孩子切换 + 管理端发码入口、5.3 课表/余额/扣课明细（三个 `/me` 只读接口 + 小程序三页 + 首页改工作台）均已完成。5.3 是阶段 5 首次动后端：**零迁移、零新表、零写路径改动**。

---

## §0 交接区（最新在最上面）

### [5.3] 2026-10-31 02:20 · commit `c20eea0`（已推送）

**我改了什么**

- **后端三个只读接口**（`app/api/routes/me.py` + 新服务层 `app/services/portal.py` + `app/schemas/me.py`，零迁移零新表）：
  - `GET /me/schedule?from=&to=` —— 窗口上限 31 天，无时区 422 `TIMEZONE_REQUIRED`，`from>=to` 422 `RANGE_INVALID`，超限 422 `RANGE_TOO_LARGE`；`cancelled` 不返回；出参 `{student_id, from, to, items[{lesson_id, class_name, subject, teacher_name, start_at, end_at, status}]}`。
  - `GET /me/balance` —— `total` 直接调 `ledger.total_balance`（实时 `SUM(delta)`），`packages` 直接调 `ledger.student_packages` 后只留 `status=active` 的项，**一处都没重算**。
  - `GET /me/ledger?type=&page=&page_size=` —— 复用 `ledger.ledger_query`，默认 `page_size=20`、上限 100（超出走 FastAPI 422），出参 `{items[{type, delta, class_name, created_at}], total}`。
- **统一闸门 `child_session`**：非家长 → 403 `OUT_OF_SCOPE`；无激活孩子 → 403 `OUT_OF_SCOPE`；激活孩子必须仍在**实时**绑定集合里（`get_parent_scope`，不认 token 快照）；通过后才开 `tenant_context` 让 RLS 兜住跨租户。三个接口都不接受客户端传的 `student_id`。
- **小程序四页**（`pages/home` 改造 + `pages/schedule` / `pages/balance` / `pages/ledger` 新增）：首页从「孩子列表」升级为工作台（顶部当前孩子，点卡进选择孩子页；下方三入口卡）；课表按天分组、`moved`「已调」/`makeup`「补课」徽章、翻周（`‹ 本周 ›`）、下拉刷新；余额大字 total（负数红色 + 欠课时提示）、包列表 30 天内到期标警示色、下拉刷新；明细类型徽章 + 增减课时、上拉加载更多、下拉刷新、空态。
- **`utils/portal.js`（新增，纯逻辑，20 项 node:test）**：周窗口按**固定 +08:00** 推算（不跟随手机时区；周日归本周、跨月不塌）、`MM-DD HH:mm` 格式化、按天分组、`moved/makeup` 徽章、类型中文名、`nextPageState`、30 天到期判定、负数余额语气、403 错误引导。
- **测试**：`tests/test_me_portal.py` **15 项**（真 DB）；`miniprogram/tests/portal.test.js` 20 项；`live-smoke.js` 从 54 项扩到 **72 项**（新增 18 条覆盖三页 + 切孩子后数据变化）。

**必须跟着改的地方**

1. **§9.3 的 `week` 字段没做**（032 裁决 3 明确不要 `?week=`）。响应里没有 `week`，改回显 `from`/`to` —— 小程序拿这两个值直接算下一窗口，不依赖服务端周解析。**要按 §9.3 原样出 `week` 就得后端补 ISO 周计算，属于新口径，别默认它已经在。**
2. **`status` 里的 `makeup` 不是 `lesson.status`。** 排课表只有 `normal/moved/cancelled`，`makeup` 是 `attendance.status`（3.4 补课排期只写考勤座，不写课次）。所以服务层是「`enrollment` 课次 ∪ 补课席位」两路合并，`status` 由补课席位反推；**`cancelled` 再加一层过滤，实际只会出现 3 个值**（`normal` / `moved` / `makeup`）。
3. **补课可以排进孩子没报名的班**（`POST /makeup-lessons` 只写 attendance），只查 `class_enrollment` 会漏课。改查询请保留这个 union，也别把 makeup 判断写成 `lesson.status == 'makeup'`（永远为假）。
4. **`/me/balance` 的 `packages` 只给仍有效的包**（`remaining>0` 且未过期），而后台 `GET /students/{id}/balance` 给全部包（含 `expired` / `exhausted`）。**两边 `total` 恒等**（同一 `SUM(delta)`），但 `packages` 条数会不一样，别用「条数相等」做验收断言。过期包那部分课时仍计入 `total`，与 `deduct_lessons` 的先过期先扣是同一份数据。
5. **未选孩子返回 403 `OUT_OF_SCOPE`，不是 `TENANT_CONTEXT_REQUIRED`。** 三个 `/me` 只读接口刻意不用 `TenantDbSession`（那个依赖会给 `TENANT_CONTEXT_REQUIRED`）。家长端只认一个码，小程序靠它跳「选择孩子」页；改回 `TENANT_CONTEXT_REQUIRED` 小程序就分流不了。
6. **解绑后 token 里的 `active_student_id` 还在，但接口会立刻 403**（实时复核绑定集合）。小程序此时给「重新选择孩子」按钮，不会再拿旧 token 查出别人的数据。
7. **`subject` 取 `course.subject`，为空时退化成 `course.name`**；`teacher_name` 走 `class.teacher_id → teacher.name`（老师被停用也照样显示名字，不隐藏历史课）。
8. **出参时间统一 `+08:00`**：库里是 `timestamptz`，服务层 `astimezone(BUSINESS_TZ)` 后才给 Pydantic，别把这层去掉（否则前端拿到 `+00:00`，`MM-DD HH:mm` 会差 8 小时）。
9. **窗口上限 31 天、报表上限 366 天，两套口径不通用**。错误码相同（`TIMEZONE_REQUIRED` / `RANGE_INVALID` / `RANGE_TOO_LARGE`），文案里的天数不同。
10. **`/me/ledger` 只给 4 列**（`type/delta/class_name/created_at`），没有 `reason` / `operator_id` / 流水 `id`。家长端不需要「谁的算子」，但也就看不到「补课转入」这类说明——要显示得给 §9.5 加字段。
11. **首页不再是孩子列表**：多孩子入口合并到顶部卡（点进 `pages/switch-student`），5.2 那个行内「切到」按钮已移除。测试里若按「首页能看到全部孩子」写断言会挂。
12. **`wx.request` 的 GET 参数走 `data`**（真机会拼成 query string）。`live-smoke.js` 的 mock 之前没做这个拼接，本轮补上了——以后加带 query 的接口，别再以为 mock 会自动处理。

**给发令部的指令**

- 5.4 若做「上课提醒 / 取消通知」，注意 `cancelled` 在这三个接口里是**不返回**的（家长看课表不需要取消的课）。要做「这节课取消了」的推送，得单独查或加 `include_cancelled=1`，不能复用现在的 `/me/schedule`。
- 032 裁决 1 砍掉的「课堂反馈」在 01~07 里确实无表无接口，二期要是做，得同时补：表 + 管理端发布入口 + 小程序查看页 + 家长可见范围（现在 `/me/*` 只有三个只读口）。
- 反馈/通知若要做，可复用 `child_session` 这套闸（角色 → 激活孩子 → 实时绑定复核 → 租户上下文），别各写一遍。
- `GET /me/balance` 与后台 `/students/{id}/packages` 的**条数口径不同**（见上第 4 条），验收脚本别互相套。

**验证（本地实跑）**

- 后端 `python -m pytest -q`（干净库 + `RUN_DB_TESTS=1` + `alembic upgrade head`）：**269 passed**（原 254 + 新增 15）。
- 小程序 `node --test`：`request-core` 15 + `session` 22 + `portal` 20 = **57 项全过**。
- 真后端冒烟 **72 项全过**，连跑 3 轮无差：登录 → 绑定 → 换身份 → 三页取数（课表 2 节含 1 节 `moved`、余额增量 +17、流水 3 条）→ 切孩子后三页数据随之变化 → 切回复原。脚本在**下一周的空档**里挑时段，同一库反复跑不撞跨班冲突。
- 静态校验（本机无微信开发者工具）：7 个页面的四件套齐全、全部 js `node --check`、全部 json 可解析、WXML 标签栈闭合。
- `web/` 一行未动（032 要求不涉及管理端），`pnpm` 相关无需重跑。


### [5.2] 2026-10-31 01:15 · commit `f43e593`（已推送；功能轮 CI `41093190247` 的 `test` job 挂在**既存并发 flake**，`lint` / `web` / `docker-smoke` 绿，详见「验证」末条）

**我改了什么**

- **小程序绑定页** `pages/bind/`（`bind-placeholder` 已删除）：8 位码输入 + 从剪贴板粘贴（`wx.getClipboardData`）+ 扫一扫（`wx.scanCode`）。成功后 `POST /me/bindings` → **整体覆盖 storage 的 auth**（role 保留、`bound_students` 回填实时条数）→ `resetSession()` → `reLaunch` 首页；失败内联显示原因 + 按错误码给不同动作按钮（重试 / 去切换孩子 / 重新登录）。
- **小程序选择孩子页** `pages/switch-student/`：`GET /me/students` 全量（跨机构也列，机构名一并显示），当前激活项高亮；点选 → `POST /me/active-student` → 覆盖 auth → `resetSession()` → `reLaunch` 首页。
- **登录分流修正**（030 指出的缺口）：`0 个绑定` → 绑定页；`≥1 个绑定且 tenant_id 为空` → **选择孩子页**；其余 → 首页。判定用 `/me/students` 的实时条数，不用登录响应的 `bound_students` 快照。
- **首页每行「切到」入口**：非当前孩子才显示，点了原地重签 JWT + 刷新列表（不 `reLaunch`），多机构来回切不用退到选择页。
- **`utils/session.js`（纯函数，可单测）**：`mergeSession`（保留 role / 回填 bound_students）、`normalizeInviteCode`、`extractInviteCode`、`describeBindError`、`describeSwitchError`。
- **web 学生页「邀请码」**：`components/InviteCodeDialog.vue` —— 该生邀请码列表（码 / 状态 / 有效期至 / 使用人摘要 / 复制）+ 生成新码（24h 默认，可选 72h / 168h）+ 生成后大字展示 + 一键复制。过期/已用的行整体置灰、复制按钮消失。操作列宽度 150 → 230。
- 测试：小程序 `session.test.js` 22 项、`live-smoke.js` 扩到 **54 项**；web Playwright **30 项**（真后端 + 本机 Edge）。

**必须跟着改的地方**

1. **后端把「码不存在 / 已用过 / 已过期」统一归成 404 `INVITE_CODE_INVALID`**，前端区分不了。031 指令写的是「404/409/422 按后端实际错误码映射」，实际只有这一个码，所以文案是三者合一句：「邀请码无效、已被使用或已过期，请向机构重新索取」。要细分就得后端拆码。
2. **`BINDING_EXISTS`（409）的引导是死胡同**：文案让用户「去切换孩子」，但选择孩子页只能切、不能解绑，而解绑只有 web 端 `DELETE /bindings/{id}`。家长真遇到「这个孩子已在我名下」时，小程序里没有出口。要么后端/小程序补解绑，要么改文案让家长找教务。
3. **`login_wechat` 只在「恰好 1 个有效绑定」时给 `tenant_id`。** 所以 `bound_students >= 2` 且 `tenant_id` 为空的家长登录后进选择孩子页 —— 这是本步修掉的 030 缺口。注意绑第二个码时后端会重签成**第二个孩子**的租户上下文。
4. **切换孩子的错误码不是 404**：403 `OUT_OF_BINDING_SET`（不在绑定集合）、403 `OUT_OF_SCOPE`（同码还表示「不是家长」和「孩子已归档」）。前端只能给一句通用提示，别指望精确到原因。
5. **两个重签接口的响应都没有 `role` 和 `bound_students`**（`MeBindingResponse` / `ActiveStudentResponse`）。`Object.assign(storage, response)` 会把 `role` 抹成 `undefined`，必须走 `mergeSession`。这是本步最容易踩的坑。
6. **绑定/切换成功后必须 `resetSession()`**，否则 5.1 里那个「清场只回调一次」的 flag 会挡住后续 401 续期。
7. **扫码结果不一定是纯码**（可能是 URL 或带说明的文本）。`extractInviteCode` 的降级顺序：纯码 → 带连字符的 `XXXX-XXXX` → 孤立的 8 位串 → 认不出就提示手动输入。
8. **`BINDING_EXISTS` 时码不会被消耗**（后端整个 `db.begin()` 回滚），教务那个码仍然可用给别人。别在外面把码标记成已用。
9. **web 不生成二维码图片**（031 禁令），只出文本码 + 复制。家长端扫码走的是 `wx.scanCode`，扫的是文本二维码。
10. **`used_by` 是 `wechat_user.id`**，教务看不懂，只显示前 8 位（指令要求「摘要」）；要显示「爸爸/妈妈」得后端补 relation，一期没有。
11. **学生页操作列变宽到 230**（多了「邀请码」按钮）。4.2 的 e2e 若断言列宽/按钮数会挂。

**给发令部的指令**

- 5.3 做课表/余额/扣课明细时，切换孩子入口已经就位：小程序端用 `utils/auth.js#switchStudent`，别再写一遍重签逻辑。
- 解绑只存在于 web 端；小程序端没有入口。要不要开，属于权限矩阵的事，建议先定「家长能否自助解绑」。
- 多孩子在**同一机构**时，切孩子后机构名不变是正常的，别把它当断言（`live-smoke.js` 里跨机构那条是软断言，单机构环境打印 `skip`）。
- 邀请码有效期后端允许 1–720 小时，前端只暴露 24/72/168 三档（按 031 指令）。要放开就改 `InviteCodeDialog.vue` 的 `HOUR_OPTIONS`。

**验证（本地实跑）**

- 小程序 `node --test`：`request-core` 15 项 + `session` 22 项，**37 项全过**。
- 真后端冒烟 **54 项全过**：新身份 `needBind=true` → 无效码 `INVITE_CODE_INVALID` + 文案 → 绑第一个（`active_student_id` 指向它、`tenant_id` 有值、**`role` 仍是 parent**、`bound_students=1`）→ 同码重绑 `BINDING_EXISTS` + 引导动作 `switch` → 绑第二个（`bound_students=2`）→ 切回第一个（`active_student_id` / `tenant_id` 同步、`bound_students` 不变）→ `/me/students` 的 `active` 标记转移 → 切到未绑定孩子报 `OUT_OF_BINDING_SET` 且原激活态没被改坏。脚本自带前置造数（学生不够就建、身份没绑定就发码），换库可直接跑。
- web Playwright **30 项全过**（真后端 + Edge）：学生行有「邀请码」、弹窗标题带姓名、默认 24 小时、过期码显示「已过期」且整行置灰且无复制按钮、有效码「可用」且有复制、生成的码形状 `AB12-CD34`、7 天档的到期时间落在 6.5–7.5 天、家长 API 兑换后刷新变「已使用」+ 使用人摘要 + 复制按钮消失、重开弹窗不残留上次的大字、老师调发码接口 403 `OUT_OF_SCOPE`、老师菜单无「学生档案」、老师直访学生页被拦、全程 0 条控制台报错。
- `pnpm typecheck`（`vue-tsc --noEmit`）与 `pnpm build` 均通过。
- **一次偶发记录**：首轮在新库上跑冒烟时，绑定第二个孩子报 `INVITE_CODE_INVALID`，但该码在库里 `used_at is null` 且未过期。随后清空业务表重建、连跑 4 次均未复现，未定位到根因。若再出现，优先查 `auth_invite_code` 与 `set_config('app.tenant_id')` 的交互。
- **CI `41093190247` 的 `test` job 失败 = 既存 flake，与本步无关**（本步 26 个文件全在 `miniprogram/`、`web/`、`docs/`；`app/`、`alembic/`、`tests/` 一行未动）。本地按 CI 同款环境复现（`APP_ENV=test` + 干净库 + `RUN_DB_TESTS=1`，`alembic upgrade head`）：

  | 轮次 | 结果 | 失败项 |
  |------|------|--------|
  | 第 1 轮（干净库） | 253 passed / 1 failed | `test_conflict.py::test_concurrent_insert_exactly_one_wins` —— 一侧拿到 `DBAPIError: deadlock detected`（40P01），不是 `AppError(409)` |
  | 该测试单独重跑 3 次 | 8 passed ×3 | 未复现 |
  | 第 2 轮（同一库连跑） | 253 passed / 1 failed | `test_ledger.py::test_reconcile_script_reports_zero_drift`（脏库残留导致，CI 每轮空库，不适用） |
  | 第 3 轮（重建干净库） | **254 passed** | — |

  结论：`EXCLUDE` 约束下两条并发插入互相等对方的 speculative insertion，PostgreSQL 会判死锁（40P01）而不是排他冲突（23P01），`create_lesson` 只把 23P01 翻成 409，于是偶发红。**这是既有并发路径的真实缺陷（生产上并发排同一时段偶发 500 而非 409），不是本步引入的**；本步按 031 禁令不动后端，只重推复跑。建议发令部单开一步修：`create_lesson` 对 40P01/40001 做一次 savepoint 内重试（重试时预检能看到已提交的胜者，自然落到 409）。

### [5.1] 2026-10-31 00:52 · commit `ee406c7`（已推送，CI 41089429270 四 job 全绿）

**我改了什么**

- 新增 `miniprogram/`（与 `web/` 平级）：原生小程序 + 原生 JS，**零 npm 依赖、零构建链**。`app/`、`alembic/`、`tests/`、`.github/` 一行未动。
- 工程骨架：`app.json`（3 页）、`project.config.json`（appid 占位 `touristappid`、`urlCheck:false`）、`sitemap.json`、`app.js` / `app.wxss`。
- `utils/request-core.js` 是请求管线的**纯逻辑**：Bearer 注入、401 单航班续期、错误统一成 `{code,message,status,detail}`。**完全不碰 `wx.*`**，所以能在 Node 里直测——这是 030「拆成纯函数配 Node 单测」的落点。
- `utils/request.js` 只做 `wx.request → Promise` 适配，把平台能力（storage / toast / reLaunch）注入 core。
- `utils/storage.js`：`auth` 一个 key，存 `access_token / refresh_token / role / tenant_id / active_student_id / bound_students`。**openid/unionid 不落地**（06 §2.1 红线）。
- `utils/auth.js`：`wx.login()` → `POST /auth/wechat-login { wx_code }` → 存 token → `GET /me/students` 验身份 → 返回 `{ needBind, students }`。
- `pages/login`：首屏；已有 token 直接 `reLaunch` 首页；失败内联显示原因 + 重试（不是弹个 toast 就没）。
- `pages/home`：`/me/students` 孩子列表（姓名 / 机构 / 关系 / 「当前」标记）+ 下拉刷新；加载 / 空 / 错三态，错误页写原因 + 重新加载，不白屏。
- `pages/bind-placeholder`：一句「请向机构索取邀请码」+「我已绑定，重新检查」+「换个微信号登录」（真绑定是 5.2）。
- 测试：`tests/request-core.test.js` 15 项（`node:test`，零框架）；`tests/live-smoke.js` 26 项真后端冒烟（手动跑）。

**必须跟着改的地方**

1. **判定去首页还是引导页，用的是 `/me/students` 的实时条数，不是登录响应的 `bound_students` 快照。** 后者是签发那一刻算的，紧接着 `/me/students` 会重新查库；中途被解绑就会出现一个说有一个说没有。现在以 `items.length` 为准，并把 `bound_students` 回填成实时值。**别再把登录响应的 `bound_students` 当作「这家长有几个孩子」的权威值。**
2. **`tenant_id === null` 不等于「没绑定」，这个分支现在会误导人。** `login_wechat` 只在「恰好 1 个有效绑定」时才给 `tenant_id`；绑了 2 个孩子时 `bound_students=2` 但 `tenant_id=null`。按 030 指令（`bound_students===0` 或 `tenant_id===null` → 绑定引导页），这类家长会被送到「请向机构索取邀请码」——但他明明绑过。**本步照指令实现了，缺口在指令侧：5.2/5.3 得补一个「选择孩子」页，或者引导页把两种文案分开。**
3. **`touristappid` 和 `wx.login` 是冲突的。** 游客模式下 `wx.login` 拿不到 code，而 stub 后端接受任意 code。折中：`utils/config.js` 在 `envVersion === 'develop'` 时允许用固定假 code（`devtools_openid|devtools_unionid`），trial / release 自动失效。**换真 appid 后兜底自然不生效，不用删也不会误用**；但若写「登录必须走真 code」的验收，要注明 develop 例外。
4. **401 清场只回调一次。** 并发 3 个 401 且 refresh 也失效时，`clearTokens` + `reLaunch` 只执行一次（内部 flag），不会连着踢三次。登录成功后调 `resetSession()` 复位。030 没写这条，是实现细节，但页面层要遵守：**别自己再 catch 401 去跳转**，交给封装。
5. **登录请求不参与续期。** `POST /auth/wechat-login` 带 `skipAuth + skipRefresh`，它自己 401 直接当业务错误抛（否则会 refresh→重放→再 401 死循环）。
6. **没有 `/auth/logout` 接口。** 退出登录只清本地 storage —— 后端 JWT 无状态，本来也没黑名单。要「服务端作废」得加表，属二期。
7. **没有 tabBar。** 3 个页面用 `reLaunch` 切换。5.3 上四主页面后再评估；**tabBar 页面不能用 `navigateTo` 跳**，加了会牵动 `app.json` 和所有跳转点。
8. **小程序不发 `tenant_id`**，所有隔离靠 JWT（06 决策 3 红线）。
9. **首页只打 `/me/students`**，课表 / 余额 / 扣课明细一个接口都没调（5.3 的活）。
10. **CI 没加小程序 job**（030 明令）。单测本地跑：`node --test miniprogram/tests/request-core.test.js`。

**给发令部的指令**

- 5.2 做绑定页时直接复用 `utils/request.js` 的 `post`。**`POST /me/bindings` 的响应里没有 `role` / `bound_students`，但会重签带 `tenant_id` 的 JWT，所以必须整体覆盖 storage 的 `auth`（保留 `role`），不能只 patch `access_token`。**
- 5.2 绑定成功后要调 `resetSession()`，否则上一轮清场留下的 flag 会挡住后续 401 续期。
- 多孩子（`bound_students > 1` 且 `tenant_id === null`）现在**没有正常出口**，建议 5.2 或 5.3 补「选择孩子」页，用 `POST /me/active-student` 换新 JWT。
- **「微信开发者工具打开能编译通过」这条本机无法自动验证（没装工具）**，已列为人工验证项；我做的是等价静态校验，见下。发令部若要把它当硬门槛，需要人工过一遍。
- 5.3 再决定 tabBar；要加就得同步改所有跳转方式。

**验证（本地实跑）**

- Node 单测 **15 项全过**：Bearer 注入、并发 3 个 401 只发 1 次 refresh（断言 `businessCalls=6`、`refreshCalls=1`）、refresh 失败清场只一次、续期后仍 401 不死循环、无 refresh token 不发请求、422 带 detail + toast、`silent` 不 toast、登录接口自身 401 不触发续期、断网 `NETWORK_ERROR`、无 token 不带 Authorization、`resetSession` 复位、singleFlight 结束后可重入。
- 真后端冒烟 **26 项全过**（嵌入式 PG + `uvicorn --port 8010`，stub 微信模式；造了一个绑 1 个孩子的家长：王小明 / 演示机构）。断言覆盖：token 落地、`role=parent`、`tenant_id`/`active_student_id` 有值、`needBind=false`、孩子姓名非空、**storage 序列化里不含 openid/unionid**、坏 token 触发真 401 → 续期 → 重放成功且没被踢、并发 3 个 401 全部恢复、refresh 失效 → `SESSION_EXPIRED` + storage 清空 + `reLaunch` 回登录页、无绑定账号 `needBind=true`、断网 `NETWORK_ERROR` 文案含「网络」、无 token 请求被挡。
- 静态校验：11 个 `.js` 过 `node --check`；6 个 `.json` 可解析；`app.json` 里 3 个页面的 `.js/.json/.wxml/.wxss` 四件套齐全；3 个 WXML 标签栈闭合。
- **未验证**：微信开发者工具编译、真机预览、三页在真机上的排版。
- CI `41089429270`：`lint` / `test` / `web` / `docker-smoke` 四 job 全绿。

### [4.4 下] 2026-10-31 00:20 · commit `c04f073`（已推送，CI 41087069037 四 job 全绿）

**我改了什么**

- 报表页从占位换成真页面：`/reports/attendance`（出勤率）、`/reports/consumption`（课耗）、`/reports/teacher-hours`（老师课时）。`/reports` 改成 redirect 到出勤率，菜单「报表」从单项变 3 个子项。路由权限 `meta.roles: ['admin','registrar']`。
- 出勤率页：班级汇总表（班级/课次数/到课/请假/缺勤/出勤率）+「按学生展开」开关（切 `detail=true`）+ 班级下拉（`class_id`）+ `notes` 用 el-alert 摆表格上方。**`attendance_rate === null` 渲染成「—」（灰色），`0` 渲染成「0%`**，两者视觉与语义都分开。
- 课耗页：学生主表 + 展开行显示该生的 `by_class` 班级分布（`student_id → rows` 建 Map，展开 O(1)）。`class_id` 为空的异常流水显示「未关联班级」，不隐藏。
- 老师课时页：老师/课次数/已完成节数/总时长，`total_minutes` 只做分钟→`8h 30m` 展示换算；页头 meta 带总时长小计。
- 三页共用 `views/reports/components/ReportToolbar.vue`：日期范围（`daterange`，`value-format=YYYY-MM-DD`）+ 本月/上月/近 30 天快捷 + 导出 CSV。**工具栏不发请求**，页面 `watch` 范围变化自行重查。
- 新增 `types/report.ts`、`api/report.ts`、`utils/report.ts`（窗口换算 + `formatRate` / `formatMinutes` / 文件名）。**零后端改动、零新增依赖**。

**必须跟着改的地方**

1. **本步偏离 029 指令一处：没引 dayjs**。指令写「用 dayjs 把本地时间 pin 到 Asia/Shanghai」，但 4.1 禁令是「禁止新增依赖」，且 dayjs 只是 element-plus 的**传递依赖**，在 pnpm 的严格 `node_modules` 下直接 `import 'dayjs'` 会解析失败（顶层没有链接）。改用 `Intl.DateTimeFormat('Asia/Shanghai')`（`utils/timetable.ts#businessToday`）+ 纯字符串日期运算（`utils/report.ts`），效果等价：默认窗口 = 业务时区本月 1 号 ~ 今天，请求时拼 `T00:00:00+08:00`。**换机器时区不会串月**（e2e 里用 `Intl` 现算日期做断言，跨零点也不会假失败）。
2. **右界要推到次日**。页面选 `10-01 ~ 10-31`，实际请求是 `from=2026-10-01T00:00:00+08:00&to=2026-11-01T00:00:00+08:00`——接口是左闭右开，不推这一天就会漏掉 10/31 当天的课。用户看到的是「含首尾」，语义转换在 `toWindowIso()` 一处。
3. **出勤率 Tab 多了班级筛选**（指令没要求）。`GET /reports/attendance-summary` 本来就支持 `class_id`，**不是新增接口**；`class_id` 与 `detail=true` 同时传时，学生明细也只算该班。
4. **导出是「当前看到的那张表」，两张表只能二选一**。指令写「当前 Tab 当前筛选结果导出」，但出勤率 Tab 有班级汇总 + 学生明细两张表：`detail` 关 → 导班级汇总（`出勤率_2026-10-01_2026-10-31.csv`）；`detail` 开 → 导学生明细（`出勤率-学生明细_...csv`）。CSV 里 `null` 出勤率写「—」，与页面一致（不写空、不写 0）。
5. **报表页没有分页器**。三个接口都不分页（`total` 就是条目数），表格直接渲染 `items`。班级下拉用 `/classes?page_size=100` 一次拉全，**超过 100 个班要后端加搜索/分页参数**，本步没加。
6. **菜单结构变了**：4.1 的「报表」单项占位已删除，`PlaceholderView` 不再承担报表。若发令部按旧菜单写断言（「报表」是 `el-menu-item`），会失败——现在是 `el-sub-menu` + 3 个 `el-menu-item`。
7. **前端一律不重算比率**。出勤率、课耗、时长全部照抄后端字段，`formatRate` 只做 `null → 「—」`，`formatMinutes` 只做除法取整。`null` 口径在页面层再算一遍就会丢。
8. **空数据不渲染空表格**：`el-empty` 与 `el-table` 互斥；加载中保留表格骨架 + `v-loading`，避免闪烁。

**给发令部的指令**

- 后续做签到页 / 工作台，窗口换算直接复用 `utils/report.ts`（`monthToDateRange` / `toWindowIso` / `isRangeValid`）与 `ReportToolbar.vue`，别再各写一份日期拼接。
- 老师课时口径是课次**实际时长**（`end_at - start_at`）而不是课程配置的 `duration_min`；前端只展示，不改口径。
- 图表（趋势线、饼图）属二期，本期纯表格，`echarts` 一行没引。
- `detail=true` 没有分页字段：学生上千的机构会一次渲染全部学生行。要「按学生分页」得后端加 `page/page_size`，前端再补分页器。

**验证（本地实跑）**

- `pnpm build`（`vue-tsc --noEmit` + `vite build`）通过；三个报表页各自独立 chunk。
- Playwright 驱动本机 Edge 打**真后端**（嵌入式 PG + uvicorn），**49 条断言全过**：菜单 3 子项、`/reports` 重定向、布局标题、4 个班级行、A 班 2/3/1/0/75%、B 班「—」、C 班 0%、makeup notes alert、按学生展开 2 人（王小明 3 到课 75% / 李小红 33%）、关闭开关表格消失、CSV 前三字节 `ef bb bf` + 含「75%」+ 文件名带日期范围、班级筛选只剩 1 行、课耗 2 人（王小明 3 课时）与展开行班级分布、老师 6 节/5 完成/8h 30m、点「上月」出 `el-empty` 且不渲染表格、teacher 菜单无报表且直访被拦 403、全程 0 条 JS 报错。
- 造数（临时脚本，未入库）：4 班 5 课次 7 条考勤 4 条 consume 流水，含 1 条未来课（只为验证 `completed_count < lesson_count`）与 1 条 makeup。
- CI `41087069037`：`lint` / `test` / `web` / `docker-smoke` 四 job 全绿。

### [4.4 上] 2026-10-30 23:58 · commit `2834336`（已推送，CI 41082564404 四 job 全绿）

**我改了什么**

- 三个**只读**报表接口，权限统一 `OfficeUser`（admin / registrar），teacher 一律 403：
  - `GET /reports/attendance-summary?from=&to=&class_id=&detail=`
  - `GET /reports/consumption?from=&to=`
  - `GET /reports/teacher-hours?from=&to=`
- 全部走 `GROUP BY` 聚合（SQLAlchemy 表达式，没有裸 SQL 拼串，也没有把明细捞进 Python 求和）。**零写操作、零迁移、不加表不加列**，表数仍 26 / 业务层 19。
- 窗口 `[from, to)` 左闭右开；入参先 `astimezone(BUSINESS_TZ)` 再进 SQL。校验：不带时区 → 422 `TIMEZONE_REQUIRED`；`from >= to` → 422 `RANGE_INVALID`；跨度 > 366 天 → 422 `RANGE_TOO_LARGE`。
- 出勤率 = `present / (present + leave + absent)`，四舍五入到整数百分比（半进位，不是银行家舍入）；**分母 0 → `null`**。`cancelled` 与「未开始」课次一律不计。`makeup` 预登记态按到课计，并在 `notes` 里写明条数。
- 课耗只认 `type='consume'`：`total_consumed = SUM(-delta)`、`lesson_count = 行数`；窗口按 `created_at`；`by_class` 用 `lesson_id → class` 取班级。
- 老师课时 `total_minutes` 用 `end_at - start_at` 的**实际差值**（不读 `course.duration_min`），`completed_count` = `start_at <= now()` 的节数。
- 新增 `tests/test_reports.py`（9 项，含一个纯函数的半进位单测）：75% / 50% / `null` 三种口径、cancelled 与未来课次不计、跨租户只看到自家、teacher 403、缺参 / 倒序 / 超跨度 / 不带时区 422。

**响应形状（029 前端直接用）**

`GET /reports/attendance-summary`（`student_rows` 只在 `detail=true` 时填充，其余情况是空数组）

```json
{
  "from": "2026-11-01T00:00:00+08:00",
  "to": "2026-12-01T00:00:00+08:00",
  "items": [
    { "class_id": "uuid", "class_name": "冲刺A班", "lesson_count": 4,
      "present": 3, "leave": 1, "absent": 0, "attendance_rate": 75 }
  ],
  "total": 1,
  "student_rows": [
    { "student_id": "uuid", "student_name": "王小明",
      "present": 3, "leave": 1, "absent": 0, "attendance_rate": 75 }
  ],
  "notes": ["有 2 条 makeup 预登记考勤，尚未转正，已按出勤计入"]
}
```

`GET /reports/consumption`

```json
{
  "from": "2026-11-01T00:00:00+08:00",
  "to": "2026-12-01T00:00:00+08:00",
  "items": [
    { "student_id": "uuid", "student_name": "王小明", "total_consumed": 8, "lesson_count": 8 }
  ],
  "total": 1,
  "by_class": [
    { "student_id": "uuid", "class_id": "uuid", "class_name": "冲刺A班",
      "consumed": 6, "lesson_count": 6 }
  ]
}
```

`GET /reports/teacher-hours`

```json
{
  "from": "2026-11-01T00:00:00+08:00",
  "to": "2026-12-01T00:00:00+08:00",
  "items": [
    { "teacher_id": "uuid", "teacher_name": "王老师",
      "lesson_count": 20, "completed_count": 12, "total_minutes": 1800 }
  ],
  "total": 1
}
```

**必须跟着改的地方**

1. **这是对 05 §8 的偏离，必须点名**：05 §8 原文是「报表一期无接口（US-213/215/302 均为二期）」。4.4 施工清单要求一期出出勤率 / 课耗 / 老师课时，故**一期扩这三个只读接口**；经营报表（`/reports/operations`）、续费预警、群发、审计日志**仍全部二期，一行没做**。
2. **`from`/`to` 必须带时区**。只传 `2026-11-01` 会 422 `TIMEZONE_REQUIRED`。前端拼「11 月」应当是 `from=2026-11-01T00:00:00+08:00&to=2026-12-01T00:00:00+08:00`。
3. **窗口左闭右开**：`to` 当天的课次不算进本月；要包含 11/30 的课，`to` 得写 12/1 0 点。
4. **出勤率 `null` ≠ 0%**：一个班有课但没人被点名时返回 `null`，前端要显示「无数据」；0% 表示点了名但全缺勤。
5. `student_rows` 字段**始终存在**，`detail` 没传时是空数组（不是 `null`、不是省略）。
6. **`attendance-summary` 只统计 `start_at <= now()` 的课次**。给未来课次预打考勤不会进报表（但 `GET /lessons/{id}/attendance` 仍能读到）。
7. `consumption.by_class` 的 `consumed` 之和**恒等于** `items.total_consumed`；`lesson_id` 为空的异常流水会以 `class_id: null` 出现，不隐藏、不吞掉。
8. **老师不开放报表**（0.2 矩阵 + 4.1 菜单本来就没有报表页）。要开属于权限矩阵变更，单独发指令。
9. **没有新增索引**。现有 `ix_lesson_tenant_id_{class,teacher,classroom}_start_at`、`ix_attendance_tenant_id_student_id`、`ix_lesson_ledger_tenant_id_{type,lesson_id}` 足够；单租户课次到十万级再评估。
10. 报表接口**不分页**：聚合结果按班级 / 学生 / 老师一行一个，单机构条目数有限。以后要「按学生展开 + 翻页」需要另设计。

**给发令部的指令**

- 029 前端报表页按上面的响应形状直接渲染，**不要在前端再算一遍出勤率**（自己拿 `present/(present+leave+absent)` 除会把 `null` 口径丢掉）。
- 时间筛选复用 `web/src/utils/timetable.ts` 的时区换算，**别用 `new Date().toISOString().slice(0, 10)`**——那是 UTC 日期，月初/月末会串月。
- 三个接口都没有分页字段，表格直接用 `items`，`total` 就是条目数。
- 029 若要做「点班级下钻到学生」，用同一个接口加 `detail=true` + `class_id`，不需要新接口。

**验证（本地实跑）**

- `RUN_DB_TESTS=1 pytest tests/test_reports.py` → **9 passed**。
- 手工 curl 三个接口 + SQL 逐项核对：A 班一节课 `1 present + 1 leave` → `attendance_rate: 50`；课耗 `SUM(-delta)` 与流水一致；老师课时 3 节 270 分钟（含 1 节未来、排除 1 节 cancelled）。
- 本地全量：`test_conflict.py` / `test_ledger.py` 在 Windows 上有既存的 event-loop 复用 flake（**在 HEAD 上同样复现，与本步无关**），其余全绿。
- CI `41082564404`：`lint` / `test` / `web` / `docker-smoke` 四 job 全绿。

### [4.3] 2026-10-30 21:40 · commit `98631ae`（已推送，CI 41067465029 全绿）

**我改了什么**

- 排课日历页 `/scheduling/timetable`：**周视图自绘网格**（7 列 × 时段，1px = 1 分钟，`08:00–22:00` 为默认窗口，有更早/更晚课次时按小时自动扩），课次块绝对定位，显示时间段 / 班级 / 老师 · 教室；状态三色——`normal` 蓝、`moved` 橙 + 「已调」角标、`cancelled` 灰 + 删除线 + 「已取消」角标。**未引任何日历库**。
- 月视图：42 格（周一起始，含上下月补格），每格只显示「N 节课」摘要（**已取消不计入**），点击任意格 → 跳到该天所在周的周视图。切周/切月/换筛选按 `from`/`to` 重算窗口（周一为一周起点）。
- 顶部筛选：班级（后端 `class_id`）、老师（**前端过滤，见下第 2 条**）、刷新、新增单次课；`上一周/今天/下一周`、`上一月/今天/下一月` 导航。
- 拖拽调课：HTML5 原生 drag（`draggable` + `dragover`/`drop`），落点 15 分钟吸附，拖拽时目标列高亮 + 虚线幽灵块显示落点时间；跨列 = 换日期（老师/教室不变，换老师/教室走「调课」表单）。
  - 松手先跑**本地预检**（老师 + 教室维度，与 3.3 `find_conflicts` 同口径）→ 有冲突直接弹明细、不请求；无冲突再 `PATCH /lessons/{id}` 成交。
  - 乐观更新 + 失败回滚：块先落到新位置，请求失败按快照弹回原时段（不会停在半路）。`409 LESSON_OVERLAP` / `SCHEDULE_CONFLICT` 的 `error.conflicts` 原样弹窗；`422 LESSON_ALREADY_DONE` 提示「已签到或已开始的课次不能再调整」。
  - 不可拖：`cancelled`、`start_at` 已过（含今天已开始的）、请求进行中的。禁用态 `draggable=false` + 悬浮说明。
- 课次详情弹窗：班级 / 老师 / 教室 / 日期 / 时间 / 状态 + 「调课」「取消课次」。取消走 `status='cancelled'`，二次确认文案「取消后该时段释放，可以重新排课；课次记录保留不删除」；已取消/已开始的按钮置灰。
- 新增单次课：班级/日期/起止时间/教室/老师，选班级自动带出该班教室与老师；`POST /lessons` 的 409 同样弹冲突明细。
- **课程维护页** `/scheduling/courses`（按裁决搭车补上）：筛选（名称关键词/状态）+ 新建/编辑/停用·启用/删除；`409 COURSE_IN_USE` 提示「只能停用」。菜单「排课管理」改为 2 个子菜单（排课日历、课程维护）。
- `api/http.ts`：`ApiError` 增加 `detail`（剥掉 `code`/`message`/`trace_id` 后原样保留），并提供 `conflicts()` 取数组——**这是本轮唯一改动既有文件的地方，向后兼容**。
- 新增 `utils/timetable.ts`（周/月窗口换算、`+08:00` ISO 构造、不依赖本机时区的星期推导）、`api/lesson.ts`、`types/lesson.ts`、`views/scheduling/*`。

**必须跟着改的地方**

1. **05 §5.2 的冲突预检接口（`POST /schedule-rules/validate`）不能用于单次调课**。它的入参是「规律形状」（`weekday` + `start_time` + `end_time` + 日期区间），而调课是「单次时段」；更要命的是 `validate_rule` 无法排除「正在被调的那节课本身」，会把课次和自己判成冲突。所以拖拽前的预检改为**前端本地算**（窗口内已加载的三维数据里比对老师/教室），落库仍由 `PATCH` 的 409 兜底。**发令部若要严格「拖前调接口」，需要后端加一个 `POST /lessons/{id}/validate`（带 `exclude_lesson_id`），本步按「不动后端」执行。**
2. **`GET /lessons` 实现里没有 `teacher_id` 参数**（05 §5.3 的表格写了 `?teacher_id=`，但 `app/api/routes/lesson.py` 只接受 `class_id`/`from`/`to`/`page`/`page_size`）。本轮老师筛选改为**拉回窗口内全量后在前端过滤**，不新增后端参数。**若后端补上 `teacher_id`，前端只需改 `api/lesson.ts` 一处。**
3. **后端不暴露「是否已有考勤记录」**：`LessonOut` 没有考勤字段，`GET /lessons` 也拿不到。所以「已有考勤的课次不可拖」这条，前端只能按 `start_at` 判断已开始的，已签到但未开始的靠 `422 LESSON_ALREADY_DONE` 兜底提示（拖拽会先请求、失败回滚 + 提示）。要提前置灰需后端在 `LessonOut` 加 `has_attendance`/`locked` 字段。
4. **学员维度的冲突前端算不出来**：需要 `class_enrollment` 数据，`GET /lessons` 不返回名单；`find_conflicts` 的 student 维度也只查「其他班」。本轮本地预检只覆盖老师 + 教室，跨班撞学生由后端 409 返回 `type=student` 明细弹窗兜底。
5. **`PATCH /lessons/{id}` 契约里没有 `class_id`**，所以「调课」表单的班级是禁用只读的，跨零点课次也提示「先取消再分别排课」（表单只能填当天时段）。
6. **05 §3.1 课程契约没有「单价」字段**（只有 `name`/`subject`/`duration_min`/`remark`/`status`）。027 指令里写「名称/科目/单价可选/状态」，与 05 契约冲突——**按 05 契约执行，没有加单价**，也没改后端。要收费口径得单独发指令连表一起动。
7. **409 的冲突明细平铺在 `error` 里**（`{error:{code,message,trace_id,conflicts:[...]}}`，来自 `app/api/errors.py` 的 `_payload`），不是 `detail` 数组。前端已按这个形状取。
8. 课表视口固定 620px（1px = 1 分钟，14 小时 = 840px 可滚），切窗口时自动滚到第一节课；新建课次后自动滚到该课次。

**给发令部的指令**

- 4.4 报表 / 签到页复用 `api/lesson.ts` 的 `listAllLessons`（自动翻页拉全窗口）与 `utils/timetable.ts` 的时区换算，**别再写 `new Date(iso).getHours()`**，那会随机器时区串日期。
- 排课页的状态口径：`cancelled` 不占时段、不计入月度统计、不可拖；`moved` 只是「被调过」的标记，仍然可再调。
- 不要再引入拖拽库/日历组件库（指令禁令），本轮原生 DnD 已验证可用。
- 若排 4.4 需要「按课次看考勤」，后端得先给 `LessonOut` 补考勤状态字段，否则前端只能一节课一次 `GET /lessons/{id}/attendance`（N+1）。
- `web/src/api/course.ts` 已补 `createCourse/updateCourse/deleteCourse`，建班链路（课程 → 班级 → 学员 → 排课）现在全通。

**验证（本轮实打实跑过）**

- `pnpm build`（`vue-tsc --noEmit` + `vite build`）通过。
- 起本地 PG（55432）+ `uvicorn` + 真数据，Playwright 驱动本机 Edge 打**真后端**，**42 条断言全过**：周视图 7 列且周一起始、月视图 42 格与「11/2 2 节课」、点月格跳周、拖拽到 14:00 成功且状态变 `moved`、拖到冲突位弹 2 行明细（老师/教室）并回弹原位、详情弹窗、取消课次后 `cancelled` + 不可拖、过去课次 `draggable=false`、新增单次课落库、再建冲突课时服务端 409 弹明细且未落库、课程新建/编辑/停用/删除、teacher 无排课菜单且直输 URL 拦到 403。

### [4.2] 2026-10-30 19:20 · commit `350b713`（已推送，CI 41044894471 全绿）

**我改了什么**

- 5 个管理页上线：`/archive/students`、`/archive/parents`、`/archive/teachers`、`/archive/classrooms`、`/classes`。菜单里「档案管理」改成 4 个子菜单（`el-sub-menu`），「班级管理」接真页面；`/archive` 重定向到学生档案。权限仍只写在路由 `meta.roles`（全部 OFFICE）。
- 统一列表三件套：筛选栏 + 表格 + 分页（抽了 `composables/usePagedList`），新建/编辑对话框，状态位二次确认。
- 学生：新建/编辑/归档（`status=archived`，文案「归档后不再显示，历史数据保留」）/恢复；`contact` 前端按 11 位手机号校验（可空）。
- 家长：新建/编辑；`409 PARENT_DUPLICATED` 回填到手机号输入框下方（不是一闪而过的 toast）；行内展示已绑定学生标签，可解绑，可「绑定学生」（`POST /bindings`）。
- 老师：新建/编辑/停用（`inactive`，文案「停用后排课与签到将受限」）/启用。
- 教室：新建/编辑 + 容量校验（正整数、≤10000），`422 CAPACITY_INVALID` 回填到容量字段。
- 班级：课程/老师/教室下拉 + 学期起止 + 容量（留空=继承教室容量，上限=教室容量，前后端双校验）；学员名单抽屉（`GET/POST/DELETE /classes/{id}/students`），满员 `CLASS_FULL`、重复 `ALREADY_ENROLLED`、非在读 `STUDENT_STATUS_INVALID`、已结课 `CLASS_ENDED` 都按后端 message 就近提示。
- 导出 CSV：各页按钮导出**当前筛选结果的全量**（翻页拉完，`page_size=100`），UTF-8 带 BOM，原生 Blob，未引库。
- 批量导入（学生、家长）：模板下载（表头 + 一行示例）→ papaparse 解析 → 逐条调既有 POST → 汇总「成功 N / 失败 M」+ 明细（数据行号从第 2 行起 + 错误码 + message）。单文件 200 行上限，超限前端拒收并禁用导入按钮；导入完成后按钮置灰，换文件才能再导（防重复提交）。
- `api/http.ts` 增强：`ApiError` 增加 `fieldErrors`，解析 FastAPI 422 的 `{detail:[{loc,msg}]}`，`loc` 去掉首段 `body` 后按字段回填。
- **开发环境通路由「绝对地址」改成「Vite 代理」**：`VITE_API_BASE_URL=/api` → `vite.config.ts` 把 `/api` 重写到 `http://localhost:8000`（`VITE_PROXY_TARGET` 可覆盖）。原因见下第 1 条。

**必须跟着改的地方**

1. **后端没有 CORS 中间件**（`app/main.py` 里没有 `CORSMiddleware`）。4.1 交接单里写的「`.env.development` 默认 `http://localhost:8000`」在联调时会被浏览器直接拦（跨域）。本轮没动后端，改前端走同源代理。**发令部要定口径**：生产部署统一 Nginx 同源反代（当前 `.env.production` 留空就是这个假设），还是要后端补 CORS。
2. **`.env.development` 已从 `http://localhost:8000` 改为 `/api`**。这是对 4.1 交接单的偏离，理由同上；`web/README.md` 已同步。
3. **班级表单的课程下拉没有维护入口**：05 §3.1 有 `/courses` 接口但本步范围不含课程页（5 页里没有课程）。现在必须先有课程才能建班，联调时是直接调接口建的一条。**若不安排课程维护页，这个洞会卡住真实使用**，请在新指令里明确由哪一步补。
4. **教室没有删除接口**（05 §2.6 只有 POST/GET/PATCH），所以教室页只有「新建/编辑」，没有删除确认。「同一套模式含删除确认」对教室不适用。
5. **家长列表接口没有绑定字段**（`ParentOut` 只有 id/name/phone/wechat_user_id/created_at）。前端用 `GET /bindings?page_size=100` + `GET /students?page_size=100` 聚合展示，未新增接口。机构家长数或绑定数超过 100 时会漏，规模上来需要后端在 `ParentOut` 里加 `bound_students`。
6. **导入是逐条 POST，不是事务**：中途失败时前面已入库；报告逐行列出，重跑会重复（学生无手机号唯一约束，家长会报 `PARENT_DUPLICATED`）。这是指令「逐条调用既有接口」的必然结果，已如实记录。
7. 学生 `contact` 的 11 位手机号规则只在前端，后端仍是 `max_length=64`。要支持座机得同时放宽前端规则。

**给发令部的指令**

- 后续页面复用 `web/src/api/*.ts` + `composables/usePagedList` + 「筛选/表格/分页」三件套，**不要另起一套列表逻辑**。
- 422 字段回显统一走 `ApiError.fieldErrors`（键 = 后端 `loc` 去掉首段 body，如 `body.capacity` → `capacity`），页面把 `serverErrors[字段]` 绑到 `el-form-item` 的 `error` 上。
- 权限只写 `meta.roles`，页面里禁止再判断角色（4.1 立的规矩继续有效）。
- 4.3 排课页需要的下拉已有现成函数：`listCourses` / `listTeachers` / `listClassrooms` / `listClasses`（`web/src/api/course.ts`、`archive.ts`）。
- 课程维护页缺口见上第 3 条，请明确排期。
- 导入导出仍是纯前端（无后端接口），后续需要「导入失败可下载错误行」这类能力时再单独发指令。

**验证（本轮实打实跑过）**

- `pnpm build`（`vue-tsc --noEmit` + `vite build`）通过。
- 起本地 PG（55432）+ `uvicorn` + `scripts/seed_demo.py`，Playwright 驱动本机 Edge 打**真后端**，46 条断言全过：5 页增改查、归档/停用状态位与二次确认、重复手机号字段级回显、绑定/解绑、班级满员 `CLASS_FULL`、学员移出、导出 CSV 前 3 字节 `ef bb bf` 且表头正确、导入 2 成功 1 失败（第 4 行 `PARENT_DUPLICATED`）、201 行被拒、teacher 直输 `/archive/students` `/classes` `/archive` 全部 403。

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
| 4.2 | `350b713` | 学生/家长/老师/教室/班级 5 个管理页 + 学员名单抽屉 + CSV 导出（BOM）+ 批量导入（papaparse，逐条 POST，200 行上限）；`ApiError.fieldErrors` 接 422 字段回显；开发环境改 Vite 代理 `/api`（后端无 CORS） |
| 4.3 | `98631ae` | 排课日历（自绘周网格 + 月概览）+ 原生拖拽调课（15 分钟吸附、本地老师/教室预检、乐观更新 + 409 明细回滚）+ 课次详情/调课/取消 + 新增单次课；搭车补课程维护页；`ApiError.detail` 透传 `error.conflicts`。零后端改动 |
| 4.4 上 | `2834336` | 三个只读报表接口：`/reports/attendance-summary`（按班级 + `detail` 按学生）、`/reports/consumption`（含 `by_class`）、`/reports/teacher-hours`；`OfficeUser` 收口；窗口 `[from,to)` 按 UTC+8，366 天上限；`null` 出勤率、makeup 计入并注明；零迁移、零写操作、零新表 |
| 4.4 下 | `c04f073` | 报表前端三页（出勤率 / 课耗 / 老师课时）：子路由 + 菜单 3 子项、共用日期工具栏、`null` 出勤率显示「—」、detail 展开学生、by_class 展开分布、分钟→`8h 30m`、CSV 导出（BOM + 日期范围文件名）、空数据 el-empty。零后端改动、零新增依赖（dayjs 未引，用 `Intl` 锁 Asia/Shanghai） |
| 5.1 | `ee406c7` | `miniprogram/` 家长端骨架：原生小程序 + 原生 JS，零依赖零构建链；`request-core.js` 纯逻辑（Bearer 注入 / 401 单航班续期 / 错误归一）配 15 项 node:test；登录页 + 首页孩子列表 + 绑定引导占位；openid/unionid 不落地；develop 环境假 wx_code 兜底。零后端改动、零迁移 |
| 5.2 | `f43e593` | 小程序绑定页（输码 / 粘贴 / 扫一扫 + 错误码分流引导）+ 选择孩子页 + 登录三分支分流 + 首页「切到」原地重签；`utils/session.js` 纯函数（`mergeSession` 保 role、扫码取码降级）配 22 项 node:test；web 学生页「邀请码」弹窗（24/72/168h、大字展示、过期置灰）。冒烟 54 项、Playwright 30 项。零后端改动、零迁移 |
| 5.3 | `c20eea0` | 家长端只读三件套：`GET /me/schedule`（31 天窗口、`cancelled` 不返回、`makeup` 由考勤座反推）、`/me/balance`（复用 ledger 的 `total_balance` / `student_packages`，只下有效包）、`/me/ledger`（复用 `ledger_query`，默认 20 上限 100）；统一 `child_session` 闸（角色 → 激活孩子 → 实时绑定复核 → 租户上下文）；小程序首页改工作台 + 课表/余额/明细三页 + `utils/portal.js` 纯逻辑。零迁移、零新表、零写路径改动 |