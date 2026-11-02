import { http } from './http'
import type {
  AdjustmentPayload,
  Balance,
  LedgerEntry,
  LedgerList,
  LedgerType,
  PackageList,
  PurchaseOut,
  PurchasePayload,
} from '@/types/ledger'

/** ---- 学生课时账本（后端 2.3 / 2.4，前端 047 补齐） ---- */

/** 学生课时包列表（余额实时求和） */
export function listPackages(studentId: string): Promise<PackageList> {
  return http
    .get<PackageList>(`/students/${studentId}/packages`, { params: { page: 1, page_size: 100 } })
    .then((r) => r.data)
}

/** 学生课时流水分页（只增不可改） */
export function listLedger(
  studentId: string,
  params: { type?: LedgerType; page: number; page_size: number },
): Promise<LedgerList> {
  return http
    .get<LedgerList>(`/students/${studentId}/ledger`, { params })
    .then((r) => r.data)
}

/** 实时余额（SUM(delta)，无余额列）+ 课时包明细 */
export function getBalance(studentId: string): Promise<Balance> {
  return http.get<Balance>(`/students/${studentId}/balance`).then((r) => r.data)
}

/** 购课登记：POST /purchases */
export function createPurchase(payload: PurchasePayload): Promise<PurchaseOut> {
  return http.post<PurchaseOut>('/purchases', payload).then((r) => r.data)
}

/** 课时调整：POST /ledger/adjustments（原因必填） */
export function createAdjustment(payload: AdjustmentPayload): Promise<LedgerEntry> {
  return http.post<LedgerEntry>('/ledger/adjustments', payload).then((r) => r.data)
}
