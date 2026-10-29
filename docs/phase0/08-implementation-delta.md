# 0.8 实现增量台账（Implementation Delta Log）

> **这份文件的用途**：`docs/phase0/01~07` 是 0.x 阶段定下的设计基线，不随实现回改。
> 凡是「代码实际做法 ≠ 设计文档写法」，一律记在这里，逐阶段追加。
> **后续出指令的人（发令部）只需要读这一份 + 04/05 原文，就能知道当前真实契约。**
> **执行的人（开发）在动表结构、动既有测试、动已上线接口前，必须先看这里。**

当前状态：**阶段 3.2 完成**，Alembic head = `0008_tenant_holiday`。

---

## 1. 硬约束：会破既有测试的地方（加表必看）

这两处是**硬编码数字**，新增/删除任何一张表都会让它们失败。改法固定，不要以为是回归：

| 位置 | 断言 | 3.2 后的值 | 说明 |
|------|------|:---:|------|
| `tests/test_models.py` | `len(BUSINESS_TABLES) == 18`（且去重后也是 18） | **19** | 业务层表清单，定义在 `app/models/__init__.py` |
| `tests/test_migrations.py` | `len(migrated["tables"]) == 25` | **26** | 迁移跑完后库里的表总数 |

加一张业务表的**完整动作清单**（缺一项测试就会红）：

1. `app/models/<x>.py` 建模型，`tenant_id` 必带。
2. `app/models/__init__.py`：import + 加入 `__all__` + 加入 `BUSINESS_TABLES`。
3. `alembic/versions/000N_<x>.py`：建表 **且** `ENABLE` + `FORCE ROW LEVEL SECURITY` **且** 两条策略（select / modify）**且** `GRANT ... TO app_user`（照 `0008_tenant_holiday.py` 抄）。
4. 改上面两个测试的硬编码数字。
5. `04-data-dictionary.md` 的「表清单与交付对照（N 张）」+ 表清单行 + 自检段的「业务层 N 张表」，以及 `04-er-diagram.md` 开头的「三层共 N 张表」和第 299 行附近的自检表。
6. 在本文件 §6 追加一行。

> 3.2 之前这里就已经不干净：`04-er-diagram.md` 自检表写「业务层 17 张表」，开头写 18，本来就互相矛盾。3.2 一并统一成 19。

---

## 2. 接口契约变更（相对 05-api-spec）

### 2.1 `POST /schedule-rules` 不再顺带生成课次（3.1 + 3.2）

- **05 原文**：响应 201 `{ "id": "uuid", "generated_lessons": 26 }`，创建时按周生成区间内课次。
- **实际**：
  - `POST /schedule-rules` → **201 `{ "items": [...], "total": n }`**，只写规律，一行一个 weekday，**不碰 lesson 表**。
  - 课次生成拆成独立接口 `POST /lessons/generate`（见 2.2）。
- **原因**：3.1 已上线该接口，3.2 不能回头改契约；且规律可先批量录入、后统一生成，两步分开更符合实际业务。

### 2.2 新增：课次批量生成（3.2）

```
POST /lessons/generate
请求  { "class_id": "uuid", "weeks": 8 }          # weeks 取值 1–52
响应 200 { "generated": 14, "skipped_holidays": 2, "skipped_existing": 16 }
```

- **窗口**：`max(该班所有规律的 start_date, 今天)` 起 `weeks` 整周。
- **逐条约束**：命中 rule.weekday，且日期 ≥ rule.start_date、≤ rule.end_date（可空表示不限）。
- **跳过节假日**：日期命中 `tenant_holiday` 的整天跳过，计入 `skipped_holidays`。
- **幂等**：同班同时刻已有课次则计入 `skipped_existing`，**不报错**；重复调用 `generated=0`。
- **返回 200 而非 201**：这是幂等操作，不是纯创建。
- 权限：`OfficeUser`（admin / registrar）。

### 2.3 新增：机构节假日（3.2）

```
POST   /tenant-holidays     { "dates": ["2026-11-05", ...], "name": "国庆节" }   → 200 {created, skipped}
GET    /tenant-holidays?year=2026                                                → {items, total}
DELETE /tenant-holidays/{id}                                                      → 204
```

- 重复日期计入 `skipped`，不报错；并发下撞唯一键转 409 `HOLIDAY_DUPLICATED`。
- 权限：`OfficeUser`（admin / registrar）。

### 2.4 补齐 05 只列了一部分的 CRUD（3.1）

`05` 的 5.1 只写了 `POST`，实际按完成标准补齐：

| 接口 | 说明 |
|------|------|
| `GET /schedule-rules?class_id=&teacher_id=&classroom_id=&weekday=&page=` | 列表，带 class_name / teacher_name / classroom_name |
| `GET /schedule-rules/{id}` | 详情 |
| `PATCH /schedule-rules/{id}` | 局部更新，合并后重新校验 |
| `DELETE /schedule-rules/{id}` | 204 |

### 2.5 `GET /lessons` 参数收窄（3.2）

- `05` 原文：`?class_id=&teacher_id=&from=&to=&page=`
- **实际**：`?class_id=&from=&to=&page=`（**没有 `teacher_id`**）。老师/家长按班级收窄的能力留给课表接口（US-101），不在本接口做。

### 2.6 尚未实现（不是忘记，是后置）

| 项 | 出处 | 归属 |
|----|------|------|
| 冲突校验 409 `SCHEDULE_CONFLICT` / `LESSON_OVERLAP`（老师/教室/学生三维度） | 05 §5.1、§5.2 | **3.3** |
| `POST /schedule-rules/validate` 冲突预检 | 05 §5.2 | 3.3 |
| PostgreSQL `EXCLUDE` 时间重叠约束 | 05 §5.1 | 3.3 |
| `POST /lessons`（单次加课）、`PATCH /lessons/{id}`（调课/取消） | 05 §5.3 | 后续 |

---

## 3. 数据字典变更（相对 04-data-dictionary）

| 表 | 变化 | 阶段 |
|----|------|------|
| `tenant_holiday` | **新增**（第 19 张业务表 / 全库第 26 张）。字段见 04 对应小节 | 3.2 |

- 表数量口径：平台层 4 + 微信层 3 + 业务层 19 = **26**。
- `tenant_holiday` 无 `updated_at` / `deleted_at`：节假日不做软删，DELETE 物理删除后可重新登记。
- 模型属性名 `holiday_date`，列名仍是 `date`（避开 `datetime.date` 遮蔽）。

---

## 4. 已锁定的实现约定（后续必须沿用，别另起一套）

| 约定 | 取值 | 出处 |
|------|------|------|
| `weekday` 口径 | **0 = 周一 … 6 = 周日**（等价 `date.weekday()`） | 3.1 |
| 业务时区 | `BUSINESS_TZ` = UTC+8，定义在 `app/services/ledger.py`，排课复用 | 3.1 |
| 时间字段 | 一律 **aware**：`datetime.combine(day, t, tzinfo=BUSINESS_TZ)`；出参 `astimezone(BUSINESS_TZ)` 统一 `+08:00`。naive 时间会炸 | 3.2 |
| 规律存储 | 一行一个 weekday，无 `weeks` 列；`weeks` 只用于推导 `end_date = start_date + 7N - 1`，不落库 | 3.1 |
| 权限收口 | 排课/节假日/课次全部 `OfficeUser`（admin / registrar）；老师、家长一律 403 `OUT_OF_SCOPE` | 0.2 矩阵 |
| 多租户 | 走 `TenantDbSession` + RLS，`tenant_id` 由登录态注入；跨租户读改删一律 **404**（不是 403） | 0.3 |
| 校验失败码 | 字段非法 422 / 引用不存在 404 / 状态冲突 409 | — |

---

## 5. 给发令部的提示

- 出指令时**不要**再写「当前 25 张表」「业务层 18 张」这类数字，实际是 26 / 19。
- 出指令时**不要**要求回到 `POST /schedule-rules` 返回 `generated_lessons`，那个契约已废。
- 只要指令涉及加表，请把 §1 的 6 步动作清单直接写进指令，否则会漏 RLS 或漏改测试数字。
- 3.3（冲突校验）开工前，本节 §2.6 的 4 项就是待办范围。

---

## 6. 变更记录

| 阶段 | commit | 内容摘要 |
|------|--------|----------|
| 1.6 | — | `invite_code` 追加（先于本文件建立，补记） |
| 3.1 | `0dd8267` | 排课规律 CRUD，`{items,total}` 契约；weekday 0–6 口径 |
| 3.2 | `4a90d5e` | `tenant_holiday` 新表 + 0008 迁移；`POST /lessons/generate`；节假日三件套；测试数字 18→19、25→26 |
