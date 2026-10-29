# 0.4 字段字典

> 与 `04-er-diagram.md` 一一对应。命名 snake_case、表名单数。
> 每张表给出：所属模块、一期/二期、索引/唯一约束、字段明细（字段名/类型/约束/说明）。

## 全局约定（不再逐表重复）

- `id`：`UUID`，`PK`，默认 `gen_random_uuid()`。
- `tenant_id`（业务层全表）：`UUID`，`NOT NULL`，`FK → tenant.id`；复合索引一律以 `tenant_id` 前缀。
- `created_at`：`TIMESTAMPTZ`，默认 `now()`。
- 业务层全表：启用 RLS + FORCE RLS（0.3 决策 2），按 `tenant_id = current_setting('app.tenant_id', true)` 过滤。
- 删除口径：档案类（student/teacher 等）删除 = 改 `status`，不物理删除。

---

## 平台层（4 张）

### tenant（租户/机构）｜平台层｜一期
索引：`UK(name)` 可空约束视运营需要。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| name | VARCHAR(128) | NOT NULL | 机构名 |
| status | VARCHAR(16) | NOT NULL 默认 'active' | active/suspended |
| created_at | TIMESTAMPTZ | 默认 now() | |

### plan（套餐）｜平台层｜二期
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| name | VARCHAR(64) | NOT NULL | 套餐名 |
| max_students | INT | NOT NULL | 学生数上限（US-402） |
| max_messages | INT | NOT NULL | 消息条数上限 |
| price | NUMERIC(10,2) | NOT NULL | 价格 |

### tenant_subscription（租户订阅）｜平台层｜二期
索引：`(tenant_id)`。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK→tenant.id | |
| plan_id | UUID | NOT NULL FK→plan.id | |
| start_date | DATE | NOT NULL | |
| end_date | DATE | NOT NULL | |
| status | VARCHAR(16) | NOT NULL 默认 'active' | |

### usage_meter（用量计量）｜平台层｜二期
索引：`UK(tenant_id, metric, period)`。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK→tenant.id | |
| metric | VARCHAR(32) | NOT NULL | student_count/msg_count |
| value | INT | NOT NULL 默认 0 | |
| period | VARCHAR(16) | NOT NULL | 如 2026-10 |

---

## 微信层（3 张）

### wechat_user（微信用户）｜微信层｜一期
索引：`UK(unionid)`。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| unionid | VARCHAR(64) | UNIQUE | 微信 unionid（跨端统一） |
| nickname | VARCHAR(64) | | |
| created_at | TIMESTAMPTZ | 默认 now() | |

### wechat_openid（openid/unionid 映射）｜微信层｜一期
索引：`UK(app_id, openid)`、`(wechat_user_id)`。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| wechat_user_id | UUID | NOT NULL FK→wechat_user.id | |
| app_id | VARCHAR(64) | NOT NULL | 小程序/服务号 appid |
| openid | VARCHAR(64) | NOT NULL | |

### subscribe_auth（订阅授权记录，预留）｜微信层｜二期
索引：`UK(tenant_id, wechat_user_id, template_id)`。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK→tenant.id | |
| wechat_user_id | UUID | NOT NULL FK→wechat_user.id | |
| template_id | VARCHAR(64) | NOT NULL | 订阅消息模板 |
| status | VARCHAR(16) | NOT NULL 默认 'authorized' | authorized/expired |

---

## 业务层（17 张，均含 tenant_id）

### student（学生）｜档案｜一期
索引：`(tenant_id, status)`、`(tenant_id, name)`。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK | |
| name | VARCHAR(64) | NOT NULL | |
| grade | VARCHAR(16) | | 年级 |
| school | VARCHAR(128) | | 在读学校 |
| status | VARCHAR(16) | NOT NULL 默认 'active' | active/suspended/archived（删除=改状态） |
| contact | VARCHAR(64) | | 联系方式 |
| created_at | TIMESTAMPTZ | 默认 now() | |

### parent（家长）｜档案｜一期
索引：`(tenant_id, phone)`。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK | |
| name | VARCHAR(64) | NOT NULL | |
| phone | VARCHAR(32) | NOT NULL | |
| wechat_user_id | UUID | FK→wechat_user.id 可空 | 微信绑定（US-001） |

### teacher（老师）｜档案｜一期
索引：`(tenant_id, status)`。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK | |
| name | VARCHAR(64) | NOT NULL | |
| phone | VARCHAR(32) | | |
| subject | VARCHAR(64) | | 可授科目（US-203） |
| status | VARCHAR(16) | NOT NULL 默认 'active' | 离职=停用不删档 |

### classroom（教室）｜档案｜一期
索引：`(tenant_id)`。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK | |
| name | VARCHAR(64) | NOT NULL | |
| capacity | INT | NOT NULL | 容量（US-216） |
| available_slots | JSONB | | 可用时段（US-216），如 `[{"weekday":1,"start":"18:00","end":"21:00"}]` |

### course（课程）｜课程班级｜一期
索引：`(tenant_id)`。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK | |
| name | VARCHAR(128) | NOT NULL | |
| subject | VARCHAR(64) | | |

### class（班级）｜课程班级｜一期
索引：`(tenant_id, course_id)`、`(tenant_id, teacher_id)`、`(tenant_id, classroom_id)`。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK | |
| course_id | UUID | NOT NULL FK→course.id | |
| teacher_id | UUID | NOT NULL FK→teacher.id | |
| classroom_id | UUID | NOT NULL FK→classroom.id | |
| name | VARCHAR(128) | NOT NULL | |
| capacity | INT | | 班级容量（US-204） |

### class_enrollment（班级学员）｜课程班级｜一期
索引：`UK(tenant_id, class_id, student_id)`、`(tenant_id, student_id)`。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK | |
| class_id | UUID | NOT NULL FK→class.id | |
| student_id | UUID | NOT NULL FK→student.id | |
| joined_at | DATE | NOT NULL | |

### course_package（课时包）｜课程班级｜一期
索引：`(tenant_id)`。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK | |
| name | VARCHAR(128) | NOT NULL | |
| total_lessons | INT | NOT NULL | 总课时 |
| gift_lessons | INT | 默认 0 | 赠课（US-205） |
| price | NUMERIC(10,2) | NOT NULL | |
| valid_months | INT | NOT NULL | 有效期·购后 N 月（US-205） |

### purchase（购课记录）｜交易｜一期
索引：`(tenant_id, student_id)`、`(tenant_id, package_id)`。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK | |
| student_id | UUID | NOT NULL FK→student.id | |
| package_id | UUID | NOT NULL FK→course_package.id | |
| quantity | INT | NOT NULL 默认 1 | |
| amount | NUMERIC(10,2) | NOT NULL | 实收（线下登记 US-205） |
| purchased_at | TIMESTAMPTZ | 默认 now() | |
| expires_at | DATE | | purchased_at + valid_months |

### lesson_ledger（课时流水·只增）｜课时账本｜一期
索引：`(tenant_id, student_id, created_at)`、`(tenant_id, type)`、`(tenant_id, lesson_id)`（流水与签到互查）。**无 updated_at、无余额列**（红线二）。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK | |
| student_id | UUID | NOT NULL FK→student.id | |
| purchase_id | UUID | FK→purchase.id；**consume 类型必填，其余类型可空** | 消费流水必须指明扣减的课时包 |
| lesson_id | UUID | FK→lesson.id；**consume 类型必填，其余类型可空** | 消费对应课次；支撑 US-004 家长明细显示班级、流水与签到互查 |
| type | VARCHAR(16) | NOT NULL | purchase/consume/refund/adjust/transfer |
| delta | INT | NOT NULL | 正=增 负=减；只插不改 |
| reason | VARCHAR(255) | | 调整/退费必填（US-209 留痕） |
| operator_id | UUID | NOT NULL | 经办人 |
| created_at | TIMESTAMPTZ | 默认 now() | 只增语义核心 |

### schedule_rule（排课规律）｜排课｜一期
索引：`(tenant_id, class_id)`、`(tenant_id, teacher_id, weekday)`、`(tenant_id, classroom_id, weekday)`（冲突校验用）。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK | |
| class_id | UUID | NOT NULL FK→class.id | |
| classroom_id | UUID | NOT NULL FK→classroom.id | |
| teacher_id | UUID | NOT NULL FK→teacher.id | |
| weekday | INT | NOT NULL | 0-6（US-207 规律排课） |
| start_time | TIME | NOT NULL | |
| end_time | TIME | NOT NULL | |
| start_date | DATE | NOT NULL | 规律生效区间 |
| end_date | DATE | | |

### lesson（课次）｜排课｜一期
索引：`(tenant_id, class_id, start_at)`、`(tenant_id, teacher_id, start_at)`、`(tenant_id, classroom_id, start_at)`。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK | |
| class_id | UUID | NOT NULL FK→class.id | |
| classroom_id | UUID | NOT NULL FK→classroom.id | |
| teacher_id | UUID | NOT NULL FK→teacher.id | |
| start_at | TIMESTAMPTZ | NOT NULL | |
| end_at | TIMESTAMPTZ | NOT NULL | |
| status | VARCHAR(16) | NOT NULL 默认 'normal' | normal/moved/makeup/cancelled（US-207 调课、US-212 补课） |

> 注：课次时间重叠的数据库级防护将在阶段 3 用 PG 排他约束（`tstzrange` + `EXCLUDE USING gist`）实现，普通唯一索引不用于重叠校验。

### attendance（签到记录）｜签到｜一期
索引：**`UK(tenant_id, lesson_id, student_id)` = 幂等键（红线三）**、`(tenant_id, student_id)`。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK | |
| lesson_id | UUID | NOT NULL FK→lesson.id | 幂等键之一 |
| student_id | UUID | NOT NULL FK→student.id | 幂等键之一 |
| status | VARCHAR(16) | NOT NULL | present/leave/absent（US-102） |
| deduct_lessons | INT | NOT NULL 默认 0 | 本次扣课时（按 org_config 规则） |
| operator_id | UUID | NOT NULL | 点名/补录经办 |
| created_at | TIMESTAMPTZ | 默认 now() | |

### parent_student（家长-学生绑定）｜档案｜一期
索引：`UK(tenant_id, parent_id, student_id)`、`(tenant_id, student_id)`（多对多 US-202）。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK | |
| parent_id | UUID | NOT NULL FK→parent.id | |
| student_id | UUID | NOT NULL FK→student.id | |
| relation | VARCHAR(16) | | 父/母/祖辈 |

### staff_account（员工账号）｜机构配置｜一期
索引：`UK(tenant_id, phone)`、`(tenant_id, role)`。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK | |
| name | VARCHAR(64) | NOT NULL | |
| phone | VARCHAR(32) | NOT NULL | |
| role | VARCHAR(16) | NOT NULL | admin/registrar/teacher（US-301） |
| teacher_id | UUID | FK→teacher.id 可空 | 老师角色关联任教身份 |
| status | VARCHAR(16) | NOT NULL 默认 'active' | |

### org_config（机构配置）｜机构配置｜一期
索引：`UK(tenant_id)`（一机构一条）。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK UNIQUE | |
| absence_deduct_rule | VARCHAR(16) | NOT NULL 默认 'none' | none=不扣/deduct=照扣，请假与缺勤通用（US-217） |
| logo | VARCHAR(255) | | 机构 logo（US-305 二期） |
| notify_template | JSONB | | 通知模板（US-305 二期） |

### refund（退费记录）｜交易｜二期
索引：`(tenant_id, student_id)`、`(tenant_id, status)`。
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | |
| tenant_id | UUID | NOT NULL FK | |
| student_id | UUID | NOT NULL FK→student.id | |
| purchase_id | UUID | NOT NULL FK→purchase.id | 退费来源 |
| amount | NUMERIC(10,2) | NOT NULL | |
| status | VARCHAR(16) | NOT NULL 默认 'pending' | pending/approved/refunded（US-303 审批） |
| approver_id | UUID | FK→staff_account.id 可空 | 审批人 |

---

## 表清单与交付对照（24 张）

| 层 | 表 | 一期 | 二期 |
|----|----|:---:|:---:|
| 平台层 | tenant | ✓ | |
| 平台层 | plan / tenant_subscription / usage_meter | | ✓ |
| 微信层 | wechat_user / wechat_openid | ✓ | |
| 微信层 | subscribe_auth | | ✓ |
| 业务层 | student / parent / teacher / classroom / course / class / class_enrollment / course_package / purchase / lesson_ledger / schedule_rule / lesson / attendance / parent_student / staff_account / org_config | ✓ | |
| 业务层 | refund | | ✓ |

> 自检：业务层 17 张表全部含 tenant_id 且复合索引从 tenant_id 起；lesson_ledger 只增无余额列、consume 流水必挂 purchase_id 与 lesson_id；attendance 唯一键保证幂等；balance_snapshot 已移除；字段与 ER 图一一对应，无悬空表/字段。
