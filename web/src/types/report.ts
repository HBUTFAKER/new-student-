/**
 * 报表接口类型（4.4 上，08 §0 的响应形状为准；05 §8 的「一期无接口」已被取代）。
 *
 * 关键是 `attendance_rate`：分母为 0 时后端给 `null`，"没数据"和"0% 出勤"
 * 是两个事实，前端绝不能把它落到 0。
 */

export interface ReportWindow {
  from: string
  to: string
}

/** ---- 出勤率 ---- */

export interface ClassAttendanceRow {
  class_id: string
  class_name: string
  lesson_count: number
  present: number
  leave: number
  absent: number
  attendance_rate: number | null
}

export interface StudentAttendanceRow {
  student_id: string
  student_name: string
  present: number
  leave: number
  absent: number
  attendance_rate: number | null
}

export interface AttendanceSummary extends ReportWindow {
  items: ClassAttendanceRow[]
  total: number
  /** 只在 `detail=true` 时填充；其余情况是空数组，不是 null */
  student_rows: StudentAttendanceRow[]
  /** makeup 预登记等口径说明，非空时用 el-alert 展示在表格上方 */
  notes: string[]
}

/** ---- 课耗 ---- */

export interface StudentConsumptionRow {
  student_id: string
  student_name: string
  total_consumed: number
  lesson_count: number
}

export interface ClassConsumptionRow {
  student_id: string
  /** 流水没有 lesson_id 时班级为空（异常行不隐藏） */
  class_id: string | null
  class_name: string | null
  consumed: number
  lesson_count: number
}

export interface ConsumptionReport extends ReportWindow {
  items: StudentConsumptionRow[]
  total: number
  /** consumed 之和恒等于 items.total_consumed */
  by_class: ClassConsumptionRow[]
}

/** ---- 老师课时 ---- */

export interface TeacherHoursRow {
  teacher_id: string
  teacher_name: string
  lesson_count: number
  /** start_at <= now 的节数 */
  completed_count: number
  /** 实际时长（分钟），不是课程配置的 duration_min */
  total_minutes: number
}

export interface TeacherHoursReport extends ReportWindow {
  items: TeacherHoursRow[]
  total: number
}
