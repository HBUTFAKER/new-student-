/** 课时账本 / 交易域类型（后端 2.3 / 2.4 / 2.5 已实现，前端 047 补齐） */

export type LedgerType = 'purchase' | 'grant' | 'consume' | 'adjust' | 'refund' | 'expire'
export type PackageStatus = 'active' | 'expired' | 'exhausted'

export interface LedgerEntry {
  id: string
  type: LedgerType
  delta: number
  purchase_id: string | null
  lesson_id: string | null
  class_name: string | null
  reason: string | null
  operator_id: string
  created_at: string
}

export interface LedgerList {
  items: LedgerEntry[]
  total: number
}

export interface PackageView {
  purchase_id: string
  course_id: string
  course_name: string
  package_name: string
  granted: number
  remaining: number
  purchased_at: string
  expires_at: string | null
  status: PackageStatus
}

export interface Balance {
  student_id: string
  total: number
  packages: PackageView[]
}

export interface PackageList {
  items: PackageView[]
  total: number
  total_remaining: number
}

export interface PurchaseOut {
  id: string
  student_id: string
  course_id: string
  package_id: string
  package_name: string
  quantity: number
  amount: string
  purchased_at: string
  expires_at: string
  balance_after: number
}

export interface PurchasePayload {
  student_id: string
  course_id: string
  lesson_count: number
  gift_lesson_count?: number
  valid_months: number
  amount: number
}

export interface AdjustmentPayload {
  student_id: string
  purchase_id: string
  delta: number
  reason: string
}

export interface RefundPayload {
  student_id: string
  purchase_id: string
  refund_lessons: number
  refund_amount: number
  reason: string
}

export interface TransferPayload {
  student_id: string
  from_class_id: string
  to_class_id: string
}

export const LEDGER_TYPE_LABELS: Record<LedgerType, string> = {
  purchase: '购入',
  grant: '赠课/补课',
  consume: '消耗',
  adjust: '调整',
  refund: '退费',
  expire: '过期',
}

export const LEDGER_TYPE_TAGS: Record<LedgerType, 'success' | 'primary' | 'danger' | 'warning' | 'info'> = {
  purchase: 'success',
  grant: 'primary',
  consume: 'info',
  adjust: 'warning',
  refund: 'danger',
  expire: 'info',
}

export const PACKAGE_STATUS_LABELS: Record<PackageStatus, string> = {
  active: '有效',
  expired: '已过期',
  exhausted: '已用完',
}

export const PACKAGE_STATUS_TAGS: Record<PackageStatus, 'success' | 'warning' | 'info'> = {
  active: 'success',
  expired: 'warning',
  exhausted: 'info',
}
