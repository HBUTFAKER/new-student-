import { http } from './http'
import type { Lesson, LessonCreatePayload, LessonPatchPayload } from '@/types/lesson'

interface Paged<T> {
  items: T[]
  total: number
}

type Query = Record<string, unknown>

/** 05 §5.3：GET /lessons?class_id=&from=&to=&page= （实现无 teacher_id 参数，老师筛选在前端做） */
export function listLessons(params: Query): Promise<Paged<Lesson>> {
  return http.get<Paged<Lesson>>('/lessons', { params }).then((r) => r.data)
}

/** 把窗口内的课次全量拉下来：课表要铺满整周/整月，不能被 page_size 截断 */
export async function listAllLessons(params: {
  class_id?: string
  teacher_id?: string
  from: string
  to: string
}): Promise<Lesson[]> {
  const collected: Lesson[] = []
  let page = 1
  for (;;) {
    const data = await listLessons({
      class_id: params.class_id || undefined,
      from: params.from,
      to: params.to,
      page,
      page_size: 100,
    })
    collected.push(...data.items)
    if (data.items.length === 0 || collected.length >= data.total) break
    page += 1
  }
  return params.teacher_id
    ? collected.filter((lesson) => lesson.teacher_id === params.teacher_id)
    : collected
}

/**
 * 05 §5.3：单次加课，409 SCHEDULE_CONFLICT 带 conflicts 明细。
 * skipToast=true 时由调用方自己展示冲突明细，不弹全局一闪而过的提示。
 */
export function createLesson(
  payload: LessonCreatePayload,
  skipToast = false,
): Promise<Lesson> {
  return http
    .post<Lesson>('/lessons', payload, { skipErrorToast: skipToast })
    .then((r) => r.data)
}

/** 05 §5.3：调课 / 取消，409 LESSON_OVERLAP 带 conflicts，422 LESSON_ALREADY_DONE */
export function patchLesson(
  id: string,
  payload: LessonPatchPayload,
  skipToast = false,
): Promise<Lesson> {
  return http
    .patch<Lesson>(`/lessons/${id}`, payload, { skipErrorToast: skipToast })
    .then((r) => r.data)
}
