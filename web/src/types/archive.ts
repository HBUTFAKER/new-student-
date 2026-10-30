/** 档案模块类型（05 §2 契约 + 08 §2 落地口径） */

export type StudentStatus = 'active' | 'suspended' | 'archived'
export type TeacherStatus = 'active' | 'inactive'

export interface Paged<T> {
  items: T[]
  total: number
}

export interface Student {
  id: string
  name: string
  grade: string | null
  school: string | null
  contact: string | null
  status: StudentStatus
  created_at: string
}

export interface StudentPayload {
  name: string
  grade?: string | null
  school?: string | null
  contact?: string | null
  status?: StudentStatus
}

export interface Parent {
  id: string
  name: string
  phone: string
  wechat_user_id: string | null
  created_at: string
}

export interface ParentPayload {
  name: string
  phone: string
}

export interface Teacher {
  id: string
  name: string
  phone: string | null
  subject: string | null
  status: TeacherStatus
  created_at: string
}

export interface TeacherPayload {
  name: string
  phone?: string | null
  subject?: string | null
  status?: TeacherStatus
}

export interface Classroom {
  id: string
  name: string
  capacity: number
  available_slots: unknown[] | null
  created_at: string
}

export interface ClassroomPayload {
  name: string
  capacity: number
}

export interface Binding {
  id: string
  parent_id: string
  student_id: string
  relation: string | null
  created_at: string
}

export const STUDENT_STATUS_LABELS: Record<StudentStatus, string> = {
  active: '在读',
  suspended: '停用',
  archived: '已归档',
}

export const TEACHER_STATUS_LABELS: Record<TeacherStatus, string> = {
  active: '在职',
  inactive: '已停用',
}

export const STUDENT_STATUS_OPTIONS: { label: string; value: StudentStatus }[] = [
  { label: '在读', value: 'active' },
  { label: '停用', value: 'suspended' },
  { label: '已归档', value: 'archived' },
]

export const GRADE_OPTIONS = [
  '一年级',
  '二年级',
  '三年级',
  '四年级',
  '五年级',
  '六年级',
  '七年级',
  '八年级',
  '九年级',
  '高一',
  '高二',
  '高三',
]
