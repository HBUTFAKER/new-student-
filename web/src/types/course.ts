/** 课程 / 班级 / 名单类型（05 §3.1–3.3） */

export type CourseStatus = 'active' | 'inactive'
export type ClassStatus = 'active' | 'ended'

export interface Course {
  id: string
  name: string
  subject: string | null
  duration_min: number | null
  remark: string | null
  status: CourseStatus
  created_at: string
}

/** 05 §3.1 契约字段：name / subject / duration_min / remark / status（无单价字段） */
export interface CoursePayload {
  name: string
  subject?: string | null
  duration_min?: number | null
  remark?: string | null
  status?: CourseStatus
}

export interface ClassItem {
  id: string
  name: string
  course_id: string
  course_name: string
  teacher_id: string
  teacher_name: string
  classroom_id: string
  classroom_name: string
  classroom_capacity: number
  capacity: number | null
  enrolled: number
  term_start: string | null
  term_end: string | null
  status: ClassStatus
  created_at: string
}

export interface ClassPayload {
  course_id: string
  teacher_id: string
  classroom_id: string
  name: string
  term_start: string
  term_end: string
  capacity?: number | null
}

export interface Enrollment {
  id: string
  student_id: string
  student_name: string
  student_status: string
  joined_at: string
}

export const CLASS_STATUS_LABELS: Record<ClassStatus, string> = {
  active: '进行中',
  ended: '已结课',
}

export const COURSE_STATUS_LABELS: Record<CourseStatus, string> = {
  active: '启用',
  inactive: '停用',
}
