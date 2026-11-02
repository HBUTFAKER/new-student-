import { http } from './http'
import type { RefundPayload, TransferPayload } from '@/types/ledger'

/** 退费登记返回（后端 RefundOut） */
export interface RefundOut {
  id: string
  type: string
  delta: number
  purchase_id: string | null
  lesson_id: string | null
  reason: string | null
  operator_id: string
  created_at: string
}

/** 转班返回（后端 TransferOut） */
export interface TransferOut {
  student_id: string
  from_class_id: string
  to_class_id: string
  joined_at: string
}

/** 补课登记返回（后端 MakeUpOut） */
export interface MakeUpOut {
  id: string
  type: string
  delta: number
  purchase_id: string | null
  lesson_id: string | null
  reason: string | null
  target_lesson_id: string
  operator_id: string
  created_at: string
}

/** 补课排期返回（后端 MakeupLessonOut） */
export interface MakeupLessonOut {
  id: string
  student_id: string
  target_lesson_id: string
  status: string
}

/** ---- 交易域（后端 2.5，前端 047 补齐） ---- */

/** 退费：POST /refunds（前端承担红色强确认） */
export function createRefund(payload: RefundPayload): Promise<RefundOut> {
  return http.post<RefundOut>('/refunds', payload).then((r) => r.data)
}

/** 转班：POST /transfers */
export function createTransfer(payload: TransferPayload): Promise<TransferOut> {
  return http.post<TransferOut>('/transfers', payload).then((r) => r.data)
}

/** 补课登记：POST /lessons/{lessonId}/make-ups（grant +1 并落到目标课次） */
export function createMakeup(lessonId: string, payload: {
  student_id: string
  target_lesson_id: string
}): Promise<MakeUpOut> {
  return http.post<MakeUpOut>(`/lessons/${lessonId}/make-ups`, payload).then((r) => r.data)
}

/** 补课排期：POST /makeup-lessons（给缺勤学生在目标课次加 makeup 座位） */
export function scheduleMakeup(payload: {
  student_id: string
  absent_lesson_id: string
  target_lesson_id: string
}): Promise<MakeupLessonOut> {
  return http.post<MakeupLessonOut>('/makeup-lessons', payload).then((r) => r.data)
}
