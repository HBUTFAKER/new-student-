/** 课后反馈（048）：老师课后写给家长看的课堂小结 */

export interface FeedbackRow {
  student_id: string
  student_name: string
  teacher_id: string
  content: string
  updated_at: string | null
}

export interface FeedbackList {
  lesson_id: string
  items: FeedbackRow[]
  total: number
}
