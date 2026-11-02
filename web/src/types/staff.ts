/** 员工账号管理（US-301）：创建/复活、停用/启用、重置密码、自助注销 */
import type { TenantRole } from '@/types/auth'

export type StaffStatus = 'active' | 'inactive'

export interface Staff {
  id: string
  name: string
  phone: string
  role: TenantRole
  status: StaffStatus
  teacher_id: string | null
  created_at: string
}

export interface StaffPayload {
  name: string
  phone: string
  role: TenantRole
  password: string
  teacher_id?: string | null
}

export interface StaffUpdatePayload {
  name?: string
  role?: TenantRole
  teacher_id?: string | null
  status?: StaffStatus
  password?: string
}

/** POST /staff 响应：复活旧账号时 ``revived=true``（HTTP 200），新建为 201 */
export interface StaffCreateResult extends Staff {
  revived: boolean
}

export const STAFF_STATUS_LABELS: Record<StaffStatus, string> = {
  active: '启用',
  inactive: '停用',
}
