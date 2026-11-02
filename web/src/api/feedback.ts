import { http } from './http'
import type { FeedbackList } from '@/types/feedback'

/** ---- 课后反馈（048） ---- */

/** 某课次全部已写反馈（没写的学生不在列表里） */
export function getLessonFeedback(lessonId: string): Promise<FeedbackList> {
  return http.get<FeedbackList>(`/lessons/${lessonId}/feedback`).then((r) => r.data)
}

/** 批量 upsert（逐条保存时传单元素数组），已存在则覆盖更新 */
export function putLessonFeedback(
  lessonId: string,
  items: { student_id: string; content: string }[],
): Promise<FeedbackList> {
  return http
    .put<FeedbackList>(`/lessons/${lessonId}/feedback`, { items })
    .then((r) => r.data)
}
