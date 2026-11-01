import { http } from './http'
import type { Paged } from '@/types/archive'
import type {
  Staff,
  StaffCreateResult,
  StaffPayload,
  StaffUpdatePayload,
} from '@/types/staff'

type Query = Record<string, unknown>

/** ---- 员工账号（040 §2） ---- */
export function listStaff(params: Query): Promise<Paged<Staff>> {
  return http.get<Paged<Staff>>('/staff', { params }).then((r) => r.data)
}

export function createStaff(payload: StaffPayload): Promise<StaffCreateResult> {
  return http.post<StaffCreateResult>('/staff', payload).then((r) => r.data)
}

export function updateStaff(id: string, payload: StaffUpdatePayload): Promise<Staff> {
  return http.patch<Staff>(`/staff/${id}`, payload).then((r) => r.data)
}

/** 自助注销（040 §2.3）：DELETE /me/account，body 带当前密码 */
export function deactivateMyAccount(password: string): Promise<void> {
  return http.delete('/me/account', { data: { password } }).then(() => undefined)
}
