# 0.8 实现增量台账 / 开发 ↔ 发令部交接单

> **这是两个窗口之间的固定通道。**
> 发令部（出指令）只读 §0；开发（我）每完成一步，在 §0 顶部插一块新的。
> 瑞哥只需要说一句「X.X 改完了」，发令部打开本文件第 20 行就能接上，不需要他转述。
>
> **读法**
> - 发令部：读 §0 最新一块 + §4 已锁定约定，再出指令。别只看 01~07 原文，那是基线，不是现状。
> - 开发：动表结构、动既有测试、动已上线接口前，先读 §1 与 §2。

当前状态：**阶段 3 收官（3.5 完成）**，Alembic head = `0010_org_config_audit`，全库 26 张表（业务层 19 张）。阶段 4 进行中：**4.1 前端骨架 + 4.2 档案与班级管理页 + 4.3 排课日历与拖拽调课 + 4.4 上（三个只读报表接口）已完成**。4.4 上是阶段 4 唯一动后端的部分：零迁移、零新表。

---

## §0 交接区（最新在最上面）

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
