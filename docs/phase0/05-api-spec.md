# 0.5 API 清单（一期全量）

> 对应《学生信息管理系统_分步执行清单》步骤 0.5。技术栈：FastAPI。
> 上游：0.1 一期范围（砍法 A）、0.2 权限矩阵、0.3 租户上下文、0.4 24 表与扣包顺序。
> 本文是一期前后端联调契约；二期接口只列名字（见末节）。

## 0. 统一约定

- **租户过滤（全接口通用）**：`tenant_id` 由 JWT 服务端解析，经 `set_config('app.tenant_id', :t, true)` 注入事务，RLS 兜底；**任何接口不接收 tenant 参数**，请求中出现 `tenant_id` 字段一律忽略。
- **数据归属（对象级鉴权）**：角色通过后再校验归属——老师仅任教班级、家长仅绑定孩子、教务/管理员本租户全量。越界对象一律 404/403。
- **认证**：`Authorization: Bearer <JWT>`。JWT 载荷：`sub`（用户ID）、`tenant_id`、`role`（admin/registrar/teacher/parent）、`scope_type`（数据归属类型）；绑定孩子集合、任教班级由服务端每次实时查 DB（防止 token 内数据过期/被篡改扩大）。access token 默认 2h，refresh token 默认 30d，二者用 `type` 声明区分。未绑定孩子的家长 token `tenant_id=NULL`，仅可访问 `/me/bindings`、`/me/students`、`/me/active-student` 三个绑定相关接口，访问业务数据一律 403（对齐 0.6）。
- **错误格式**：
```json
{ "error": { "code": "STUDENT_NOT_FOUND", "message": "学生不存在", "trace_id": "8f3a…" } }
```
- **HTTP 映射**：400 参数错误｜401 未认证/token 无效｜403 角色不符或越权｜404 资源不存在或越界资源｜409 冲突（唯一约束，签到除外——见幂等）｜422 请求体校验失败。
- **分页**：`?page=1&page_size=20`（max 100），响应 `{ "items": [...], "total": 123 }`。
- **时间**：ISO 8601 带时区，如 `2026-11-03T19:00:00+08:00`。
- **ID**：一律 UUID v4。

---

## 1. 认证登录模块

### 1.1 `POST /auth/login` — 员工账密登录
- **权限**：公开（机构内员工）。
- **请求**：`{ "phone": "13800000000", "password": "…" }`
- **响应 200**：`{ "access_token": "eyJ…", "refresh_token": "eyJ…", "token_type": "bearer", "expires_in": 7200, "role": "registrar", "tenant_id": "uuid" }`
- **错误**：401 `{ "error": { "code": "AUTH_FAILED", "message": "手机号或密码错误" } }`
- **说明**：手机号仅租户内唯一。同一手机号命中多个租户时，用密码匹配唯一账号；若仍无法唯一判定，按 `AUTH_FAILED` 拒绝，后续以机构码补强。

### 1.2 `POST /auth/wechat-login` — 家长微信登录
- **权限**：公开。code 换 openid/unionid，定位 `wechat_user`；新用户自动建 `wechat_user`。家长 JWT 的 tenant_id 取当前激活孩子所属机构；无绑定/未选择孩子时为 NULL，仅开放绑定与孩子集合接口（详见 0.6）。
- **请求**：`{ "wx_code": "wechat_jscode" }`
- **响应 200**：`{ "access_token": "eyJ…", "refresh_token": "eyJ…", "token_type": "bearer", "expires_in": 7200, "role": "parent", "tenant_id": "uuid|null", "active_student_id": "uuid|null", "bound_students": 0 }`
- **错误**：401 `{ "error": { "code": "WX_AUTH_FAILED", "message": "微信授权失败" } }`；401 `WX_IDENTITY_CONFLICT`（本次 unionid 与已存身份冲突，停止自动归并）

### 1.3 `POST /auth/refresh` — 刷新 token
- **权限**：持有效 refresh_token。
- **请求**：`{ "refresh_token": "eyJ…" }`
- **响应 200**：`{ "access_token": "eyJ…", "refresh_token": "eyJ…", "token_type": "bearer", "expires_in": 7200 }`；**错误**：401 `TOKEN_EXPIRED` / `TOKEN_INVALID`

## 2. 档案模块

> **1.6 落地说明**：2.1–2.6 全部接口、2.3 教务直绑、2.4 邀请码、9.1 家长自助绑定已实现并通过阶段验收测试。差异与实现口径：
> - 删除一律为状态位：学生 `archived`、老师 `inactive`、绑定才物理删（`DELETE`）。
> - 错误码实际取值：`409 PARENT_DUPLICATED`（同租户手机号唯一）、`422 STUDENT_ARCHIVED`、`409 BINDING_EXISTS`、`404 STUDENT_NOT_FOUND` / `PARENT_NOT_FOUND` / `TEACHER_NOT_FOUND` / `BINDING_NOT_FOUND`、`422 CAPACITY_INVALID`、`404 INVITE_CODE_INVALID`、`422 EXPIRES_INVALID`、`403 OUT_OF_SCOPE`、`403 TENANT_CONTEXT_REQUIRED`。
> - 老师侧收窄是**默认行为**而非参数：`GET /students`、`GET /classes`、`GET /classrooms` 及 `GET /students/{id}` 都按 `Class.teacher_id` 过滤，非任教资源返回 404 或 403。
> - 解绑立即生效：每次请求都回库复核绑定集合，不使用 token 内的缓存快照。

### 2.1 学生档案
| 接口 | 权限 |
|------|------|
| `POST /students` | ✅教务/管理员 |
| `GET /students`（?name=&grade=&status=&page=） | ✅教务/管理员；⚠️老师：结果自动收窄为任教班级学生 |
| `GET /students/{id}` | ✅教务/管理员；⚠️老师仅任教班级；⚠️家长仅绑定孩子 |
| `PATCH /students/{id}`（含 status：停用/归档=删除口径，不物理删） | ✅教务/管理员 |

- **POST 请求**：`{ "name": "王小明", "grade": "八年级", "school": "三中", "contact": "138…", "status": "active" }`
- **POST 响应 201**：`{ "id": "uuid", "name": "王小明", "status": "active", "created_at": "…" }`
- **GET 响应 200**：`{ "items": [ … ], "total": 10 }`
- **错误**：404 `STUDENT_NOT_FOUND`；403 `{ "code": "OUT_OF_SCOPE", "message": "非任教班级学生" }`

### 2.2 家长档案
| 接口 | 权限 |
|------|------|
| `POST /parents` | ✅教务/管理员（家长自助维护本人联系方式为二期） |
| `GET /parents`（?phone=&page=） | ✅教务/管理员 |
| `PATCH /parents/{id}` | ✅教务/管理员 |

- **请求**：`{ "name": "王大力", "phone": "139…", "wechat_user_id": null }`
- **响应 201**：`{ "id": "uuid", "name": "王大力", "phone": "139…" }`
- **错误**：422 `PHONE_INVALID`；409 `PARENT_DUPLICATED`（同手机号已存在）。

### 2.3 家长-学生绑定（多对多，US-202）
| 接口 | 权限 |
|------|------|
| `POST /bindings` | ✅教务/管理员（家长自助绑定走家长端 6.1，凭邀请码） |
| `GET /bindings`（?student_id= 或 ?parent_id=） | ✅教务/管理员 |
| `DELETE /bindings/{id}` | ✅教务/管理员 |

- **POST 请求**：`{ "parent_id": "uuid", "student_id": "uuid", "relation": "父" }`
- **POST 响应 201**：`{ "id": "uuid", "relation": "父" }`
- **错误**：409 `BINDING_EXISTS`（重复绑定）；422 `STUDENT_ARCHIVED`（归档学生不可绑）。

### 2.4 邀请码（家长自助绑定的前置，US-001）
| 接口 | 权限 |
|------|------|
| `POST /invite-codes` | ✅教务/管理员 |
| `GET /students/{id}/invite-codes` | ✅教务/管理员 |

- **POST 请求**：`{ "student_id": "uuid", "expires_in_hours": 24 }`
- **POST 响应 201**：`{ "code": "AB12-CD34", "student_id": "uuid", "expires_at": "2026-11-01T10:00:00+08:00" }`
- **规则**：一次性使用；默认 24 小时有效，使用后作废；同一学生可同时存在多个有效码（爸妈各绑一次）。
- **GET 响应 200**：`{ "items": [ { "code": "AB12-CD34", "status": "used", "used_by": "wechat_user_uuid" }, { "code": "EF56-GH78", "status": "valid", "expires_at": "…" } ] }`（列出该学生的有效/已用码，便于教务核对）
- **错误**：404 `STUDENT_NOT_FOUND`；422 `EXPIRES_INVALID`。

### 2.5 老师档案
| 接口 | 权限 |
|------|------|
| `POST /teachers` | ✅教务/管理员 |
| `GET /teachers`（?status=&page=） | ✅教务/管理员；⚠️老师：GET /teachers/me 仅本人 |
| `GET /teachers/me` | ⚠️老师：仅本人档案 |
| `PATCH /teachers/{id}`（离职=停用，不删档） | ✅教务/管理员 |

- **请求**：`{ "name": "李老师", "phone": "137…", "subject": "物理" }`
- **响应 201**：`{ "id": "uuid", "name": "李老师", "status": "active" }`
- **错误**：404 `TEACHER_NOT_FOUND`。

### 2.6 教室档案（US-216）
| 接口 | 权限 |
|------|------|
| `POST /classrooms` | ✅教务/管理员 |
| `GET /classrooms` | ✅教务/管理员；⚠️老师：仅任教班级涉及教室 |
| `PATCH /classrooms/{id}` | ✅教务/管理员 |

- **请求**：`{ "name": "多媒体教室", "capacity": 40, "available_slots": [ { "weekday": 1, "start": "18:00", "end": "21:00" } ] }`
- **响应 201**：`{ "id": "uuid", "name": "多媒体教室", "capacity": 40 }`
- **错误**：422 `CAPACITY_INVALID`。

## 3. 课程班级模块

### 3.1 课程
| 接口 | 权限 |
|------|------|
| `POST /courses` / `GET /courses` / `PATCH /courses/{id}` | ✅教务/管理员 |

- **请求**：`{ "name": "初三物理冲刺", "subject": "物理" }`；**响应 201**：`{ "id": "uuid", "name": "初三物理冲刺" }`

### 3.2 班级
| 接口 | 权限 |
|------|------|
| `POST /classes`（course_id+teacher_id+classroom_id+capacity） | ✅教务/管理员 |
| `GET /classes`（?course_id=&page=） | ✅教务/管理员；⚠️老师：结果自动收窄为任教班级 |
| `PATCH /classes/{id}` | ✅教务/管理员 |

- **请求**：`{ "course_id": "uuid", "teacher_id": "uuid", "classroom_id": "uuid", "name": "冲刺A班", "capacity": 12 }`
- **响应 201**：`{ "id": "uuid", "name": "冲刺A班", "capacity": 12 }`
- **错误**：422 `CAPACITY_EXCEEDED`（容量低于已报人数）。
- **进度**：`GET /classes` 已随 1.6 落地（只读，老师自动收窄为任教班级）；`POST`/`PATCH` 待 1.7 课程班级模块。

### 3.3 班级学员名单
| 接口 | 权限 |
|------|------|
| `GET /classes/{id}/students` | ✅教务/管理员；⚠️老师仅任教班级（US-101 名单） |
| `POST /classes/{id}/students` | ✅教务/管理员 |
| `DELETE /classes/{id}/students/{student_id}` | ✅教务/管理员 |

- **POST 请求**：`{ "student_id": "uuid" }`；**响应 201**：`{ "id": "uuid", "joined_at": "2026-11-01" }`
- **错误**：409 `ALREADY_ENROLLED`；409 `CLASS_FULL`（超出班级容量）。

### 3.4 课时包
| 接口 | 权限 |
|------|------|
| `POST /course-packages` / `GET /course-packages` / `PATCH /course-packages/{id}` | ✅教务/管理员 |

- **请求**：`{ "name": "30 次卡", "total_lessons": 30, "gift_lessons": 2, "price": 3600.00, "valid_months": 6 }`
- **响应 201**：`{ "id": "uuid", "name": "30 次卡", "valid_months": 6 }`
- **错误**：422 `VALID_MONTHS_REQUIRED`（有效期必填，US-205）。

### 3.5 缴费登记（线下收款，US-205）
- `POST /purchases` — ✅教务/管理员
- **请求**：`{ "student_id": "uuid", "package_id": "uuid", "quantity": 1, "amount": 3600.00 }`
- **响应 201**：
```json
{ "id": "uuid", "student_id": "uuid", "package_id": "uuid",
  "expires_at": "2027-05-01",
  "ledger_entry": { "type": "purchase", "delta": 32, "purchase_id": "uuid" } }
```
- **说明**：登记成功后**同一事务**写入 `lesson_ledger` 一条 `purchase` 流水（total+gift 课时），余额随之增加。
- **错误**：404 `PACKAGE_NOT_FOUND`；422 `AMOUNT_INVALID`。

## 4. 课时账本模块

### 4.1 流水查询
- `GET /students/{student_id}/ledger`（?type=&page=） — ✅教务/管理员；⚠️老师仅任教班级消耗流水；⚠️家长仅绑定孩子（家长端入口见 6.4）
- **响应 200**：
```json
{ "items": [ { "id": "uuid", "type": "consume", "delta": -1, "purchase_id": "uuid",
  "lesson_id": "uuid", "class_name": "冲刺A班", "operator_id": "uuid",
  "created_at": "2026-11-03T21:00:00+08:00" } ], "total": 25 }
```
- **说明**：consume 流水带 `purchase_id`（扣哪个包）与 `lesson_id`（哪次课，可回查班级名）。

### 4.2 余额查询（实时计算，无余额列）
- `GET /students/{student_id}/balance` — ✅教务/管理员；⚠️老师仅任教班级；⚠️家长仅绑定孩子
- **响应 200**：
```json
{ "student_id": "uuid", "total": 17,
  "packages": [ { "purchase_id": "uuid", "package_name": "30 次卡",
    "remaining": 12, "expires_at": "2027-05-01" } ] }
```
- **说明**：余额 = 对该学生全部流水 `SUM(delta)` 实时聚合（0.4 决策：不设快照表、无余额列）；各包余额按 purchase 分组求和。
- **错误**：403 `OUT_OF_SCOPE`（非绑定孩子/非任教班级）。

### 4.3 课时调整（US-209，敏感操作）
- `POST /ledger/adjustments` — ⚠️教务（必填 reason+全程留痕）；✅管理员
- **请求**：`{ "student_id": "uuid", "purchase_id": "uuid", "delta": -1, "reason": "投诉退赔 1 课时" }`
- **响应 201**：`{ "id": "uuid", "type": "adjust", "delta": -1, "reason": "…", "operator_id": "uuid" }`
- **错误**：422 `REASON_REQUIRED`（原因必填）；409 `INSUFFICIENT_BALANCE`（调整后余额为负）。

## 5. 排课模块

### 5.1 规律排课（US-207）
- `POST /schedule-rules` — ✅教务/管理员
- **请求**：`{ "class_id": "uuid", "classroom_id": "uuid", "teacher_id": "uuid", "weekday": 2, "start_time": "19:00:00", "end_time": "21:00:00", "start_date": "2026-11-02", "end_date": "2027-01-31" }`
- **响应 201**：`{ "id": "uuid", "generated_lessons": 26 }`
- **说明**：创建时按周生成区间内课次；老师/教室/学生三维度冲突校验（学生维度=同班学员与其他班课次重叠）。
- **错误**：409 `SCHEDULE_CONFLICT`，`{ "conflicts": [ { "type": "teacher", "lesson_id": "uuid", "start_at": "…", "end_at": "…" } ] }`（返回冲突对象）。

### 5.2 冲突预检
- `POST /schedule-rules/validate` — ✅教务/管理员；⚠️老师仅自身时段（不落库，仅返回冲突报告）
- **请求**：同 5.1；**响应 200**：`{ "ok": false, "conflicts": [ … ] }`

### 5.3 课次管理（单次调课，US-207）
| 接口 | 权限 |
|------|------|
| `GET /lessons`（?class_id=&teacher_id=&from=&to=&page=） | ✅教务/管理员；⚠️老师：自动收窄为本人任教课次（US-101 课表） |
| `POST /lessons`（单次加课） | ✅教务/管理员 |
| `PATCH /lessons/{id}`（调时间/教室/老师，status=moved；cancelled=取消） | ✅教务/管理员 |

- **PATCH 请求**：`{ "start_at": "2026-11-04T19:00:00+08:00", "end_at": "2026-11-04T21:00:00+08:00", "classroom_id": "uuid" }`
- **响应 200**：`{ "id": "uuid", "status": "moved" }`
- **错误**：409 `LESSON_OVERLAP`（与既有课次重叠）；422 `LESSON_ALREADY_DONE`（已上课次不可调）。

## 6. 签到模块

### 6.1 课堂点名签到·扣课（US-102/208，核心接口）
- `POST /lessons/{lesson_id}/attendance` — ⚠️老师仅任教班级课次；✅教务/管理员（补录同一接口）
- **请求**：
```json
{ "records": [ { "student_id": "uuid-a", "status": "present" },
               { "student_id": "uuid-b", "status": "leave" },
               { "student_id": "uuid-c", "status": "absent" } ] }
```
- **响应 200**：
```json
{ "lesson_id": "uuid", "results": [
  { "student_id": "uuid-a", "status": "present", "deduct_lessons": 1, "deduplicated": false },
  { "student_id": "uuid-b", "status": "leave", "deduct_lessons": 1, "deduplicated": false },
  { "student_id": "uuid-c", "status": "absent", "deduct_lessons": 1, "deduplicated": true },
  { "student_id": "uuid-d", "status": "failed", "reason": "BALANCE_EXHAUSTED" } ] }
```
> 示例为 deduct 模式：leave 与 absent 均扣 1 课时（none 模式下两者 deduct_lessons 均为 0）。
- **幂等实现**：`attendance` 唯一键 `(tenant_id, lesson_id, student_id)`；重复请求（重复点击/断网重试/教务补录撞老师已签）**命中唯一键即返回已有结果**（`deduplicated: true`），不报错、不重复扣课。签到与扣课写流水在同一事务（红线三）。
- **扣包顺序**：按 0.4 第四节规则——先过期先扣（`expires_at` 升序）→ 同日到期先购先扣（`purchased_at` 升序）→ 单包不足顺延下一有效包、拆多条 `consume` 流水（各挂各自 `purchase_id`/`lesson_id`）。
- **请假/缺勤扣课规则（通用）**：`status=leave` 与 `status=absent` **一致处理**，是否扣课时均按 `org_config.absence_deduct_rule`（none/deduct，US-217）；扣则照常写 consume 流水；none 模式下两者 `deduct_lessons` 均为 0。
- **逐学生判定（部分成功）**：名单内每个学生独立判定，无有效余额的学生该条返回 `{ "status": "failed", "reason": "BALANCE_EXHAUSTED" }`，不阻塞其他学生；failed 不写入 attendance，可先购课再对该生重新提交（幂等保证已成功学生不重扣）。
- **错误**：403 `OUT_OF_SCOPE`（非任教班级课次）；422 `LESSON_NOT_STARTED`。（BALANCE_EXHAUSTED 不再是整批 409，改为逐条 failed 原因）

### 6.2 点名结果查询
- `GET /lessons/{lesson_id}/attendance` — ✅教务/管理员；⚠️老师仅任教班级课次
- **响应 200**：`{ "items": [ { "student_id": "uuid", "status": "present", "deduct_lessons": 1, "operator_id": "uuid" } ], "total": 10 }`

### 6.3 请假/缺勤扣课规则配置（US-217 通用规则）
- `GET /org-config/absence-deduct-rule` — ✅教务/管理员；⚠️老师可读（点名界面提示用）；❌家长不可见
- `PATCH /org-config/absence-deduct-rule` — ⚠️教务（可改+留痕）；✅管理员
- **PATCH 请求**：`{ "absence_deduct_rule": "deduct" }`
- **响应 200**：`{ "absence_deduct_rule": "deduct", "updated_by": "uuid" }`
- **说明**：一处配置全机构生效，请假/缺勤通用；改动写操作审计记录；一期若无界面展示，仅服务端签到逻辑使用，家长端不展示。
- **错误**：422 `RULE_VALUE_INVALID`（仅 none/deduct）。

## 7. 交易模块

### 7.1 转班（US-210）
- `POST /transfers` — ✅教务/管理员（留痕）
- **请求**：`{ "student_id": "uuid", "from_class_id": "uuid", "to_class_id": "uuid" }`
- **响应 201**：
```json
{ "id": "uuid", "student_id": "uuid", "from_class_id": "uuid", "to_class_id": "uuid",
  "ledger_entry": { "type": "transfer", "delta": 0, "reason": "A班→B班" } }
```
- **说明**：同一事务内——原班名单剔除、新班名单加入、写 `transfer` 流水（reason 留痕）；两班余额各按流水求和天然正确。
- **错误**：409 `NOT_ENROLLED`（学生不在原班）；409 `CLASS_FULL`；422 `SAME_CLASS`。

## 8. 报表模块

一期无接口（US-213/215/302 均为二期）。二期见第 11 节。

## 9. 家长端模块（小程序·只读三件套）

### 9.1 自助绑定孩子（US-001，凭教务生成的邀请码，见 2.4）
- `POST /me/bindings` — ⚠️家长：仅凭有效邀请码绑定（区别于教务侧 /bindings 直绑）
- **请求**：`{ "invite_code": "AB12-CD34" }`
- **响应 201**：`{ "student_id": "uuid", "student_name": "王小明", "relation": "家长", "tenant_id": "uuid", "active_student_id": "uuid", "access_token": "eyJ…", "refresh_token": "eyJ…", "token_type": "bearer", "expires_in": 7200 }`
  （1.6 落地：绑定成功的同一个事务内消耗邀请码、建 `parent`、写 `parent_student`，并**直接重签 JWT** 让 `tenant_id` 跟随新孩子，家长无需二次登录）
- **错误**：404 `INVITE_CODE_INVALID`（无效/过期/已用）；409 `BINDING_EXISTS`；422 `STUDENT_ARCHIVED`。

### 9.2 绑定孩子列表与切换（US-010）
- `GET /me/students` — ⚠️家长：仅返回当前微信号绑定集合
- **响应 200**：`{ "items": [ { "student_id": "uuid", "student_name": "王小明", "tenant_id": "uuid", "tenant_name": "示例机构", "relation": "家长", "active": true } ] }`
- `POST /me/active-student` — ⚠️家长：目标必须在绑定集合内（V-08：集合外 403）
- **请求**：`{ "student_id": "uuid" }`；**响应 200**：`{ "active_student_id": "uuid", "tenant_id": "uuid", "access_token": "eyJ…（重签，含新 tenant_id）", "refresh_token": "eyJ…", "token_type": "bearer", "expires_in": 7200 }`（切换后必须重签 JWT，使 tenant_id 跟随当前孩子所属机构，见 0.6）
- **错误**：403 `OUT_OF_BINDING_SET`。

### 9.3 周课表（US-002）
- `GET /me/schedule`（?week=2026-W45 或 ?from=&to=） — ⚠️家长：仅当前激活孩子
- **响应 200**：
```json
{ "student_id": "uuid", "week": "2026-W45", "items": [
  { "lesson_id": "uuid", "class_name": "冲刺A班", "subject": "物理",
    "teacher_name": "李老师", "start_at": "2026-11-03T19:00:00+08:00",
    "end_at": "2026-11-03T21:00:00+08:00", "status": "normal" } ] }
```
- **说明**：moved/makeup 状态以 `status` 标记，前端高亮异常安排。

### 9.4 课时余额（US-003）
- `GET /me/balance` — ⚠️家长：仅当前激活孩子；计算方式同 4.2（实时 SUM(delta)）
- **响应 200**：`{ "student_id": "uuid", "total": 17, "packages": [ { "purchase_id": "uuid", "remaining": 12, "expires_at": "2027-05-01" } ] }`

### 9.5 扣课明细（US-004）
- `GET /me/ledger`（?type=&page=） — ⚠️家长：仅当前激活孩子；数据同 4.1 consume 含班级名
- **响应 200**：`{ "items": [ { "type": "consume", "delta": -1, "class_name": "冲刺A班", "created_at": "…" } ], "total": 25 }`

## 10. 机构配置模块

### 10.1 员工账号与角色（US-301）
| 接口 | 权限 |
|------|------|
| `POST /staff-accounts`（role=admin/registrar/teacher，teacher 角色须关联 teacher_id） | ✅机构管理员 |
| `GET /staff-accounts`（?role=&page=） | ✅机构管理员 |
| `PATCH /staff-accounts/{id}`（改角色/停用） | ✅机构管理员 |

- **POST 请求**：`{ "name": "张教务", "phone": "136…", "role": "registrar", "teacher_id": null }`
- **响应 201**：`{ "id": "uuid", "role": "registrar", "status": "active" }`
- **错误**：422 `ROLE_INVALID`；409 `PHONE_EXISTS`；422 `TEACHER_ID_REQUIRED`（teacher 角色未关联老师）。

## 11. 二期接口（只列名，不写细节）

- 家长端：`PATCH /me/profile`（自助改联系方式）、`GET /me/feedbacks`（课堂反馈）、`POST /me/leave-requests`（请假）、`GET /me/leave-requests`（请假/补课记录）、`POST /orders` + `POST /orders/{id}/pay`（在线支付）
- 老师端：`POST /lesson-feedbacks`、`POST /teacher-leave-requests`（请假/调课申请）、`GET /students/{id}/feedbacks`（历史反馈）、`GET /teachers/me/free-slots`（空闲时段）、`POST /makeup-lessons`（补课排期）
- 教务端：`POST /refunds`（退费）、`POST /refunds/{id}/approve`（审批）、`GET /reports/renewal-warnings`（续费预警）、`GET /reports/attendance-summary`（日报月报）、`POST /notifications/broadcast`（群发）、`POST /lessons/{id}/qr-checkin`（二维码签到）
- 消息：`POST /notifications/subscribe`（订阅消息授权记录）、发送上课提醒/扣课通知（内部任务）
- 管理员端：`GET /reports/operations`（经营报表）、`GET /audit-logs`（审计日志）、`PATCH /org-config`（机构信息/通知模板）、`POST /campuses`（多校区）、候补名单相关
- 平台侧：`POST /platform/tenants`（自助开通）、`GET /platform/tenants`、`GET /platform/usage`、`POST /platform/plans`、`POST /platform/tenants/{id}/service-account-auth`（机构服务号授权）、`GET /platform/dashboard`
  - **注**：`POST /platform/tenants` 的基础开通能力（建租户 + 默认套餐 + 机构配置 + 首个管理员，单事务、同名 409）已于 1.5 落地，仅限内部平台管理员调用；商业化（套餐变更、用量计费、自助注册流程）仍在二期。

## 12. 覆盖对照自检

- 0.2 矩阵一期各 ✅/⚠️ 操作 ↔ 本文接口：档案 6 组（学生/家长/绑定/邀请码/老师/教室）、课程班级 5 组（课程/班级/名单/课时包/缴费）、账本 3 条（流水/余额/调整）、排课 3 条（规律/预检/调课）、签到 4 条（点名/结果/规则读/规则写）、交易 1 条（转班）、家长端 5 条（绑定/切换/课表/余额/明细）、机构配置 1 条（员工账号）、认证 3 条——**双向无遗漏**。
- 报表/消息一期无接口（全二期），矩阵对应行均已标二期。
- 全接口租户过滤方式统一（第 0 节），数据归属条件在各接口标注，与 0.2 矩阵 ⚠️ 条款一一对应。
