/** 课次 / 冲突（05 §5.1–5.3，后端 3.2–3.4 已实现） */

export type LessonStatus = 'normal' | 'moved' | 'cancelled'
export type ConflictType = 'teacher' | 'classroom' | 'student'

export interface Lesson {
  id: string
  class_id: string
  class_name: string
  classroom_id: string
  classroom_name: string
  teacher_id: string
  teacher_name: string
  /** 后端统一回 +08:00 业务时区的 ISO 串，显示直接截字符，不经过本机时区 */
  start_at: string
  end_at: string
  status: LessonStatus
}

/** 后端 conflicts 数组原样（3.3 ConflictOut.to_dict）：type/lesson_id/class_id/start_at/end_at */
export interface ConflictItem {
  type: ConflictType
  lesson_id: string
  class_id: string
  start_at: string
  end_at: string
}

/** 界面上展示用的冲突行：后端字段原样 + 从已加载课次补的名字，不删任何字段 */
export interface ConflictRow extends ConflictItem {
  class_name?: string
  classroom_name?: string
  teacher_name?: string
}

export interface LessonCreatePayload {
  class_id: string
  classroom_id: string
  teacher_id: string
  start_at: string
  end_at: string
}

/** PATCH 只允许时间/教室/老师，或 status=cancelled（3.4 契约） */
export interface LessonPatchPayload {
  start_at?: string
  end_at?: string
  classroom_id?: string
  teacher_id?: string
  status?: 'cancelled'
}

export const LESSON_STATUS_LABELS: Record<LessonStatus, string> = {
  normal: '正常',
  moved: '已调',
  cancelled: '已取消',
}

export const CONFLICT_TYPE_LABELS: Record<ConflictType, string> = {
  teacher: '老师',
  classroom: '教室',
  student: '学员',
}
