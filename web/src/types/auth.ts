/** 员工角色，与后端 JWT 的 role 取值一致（0.2 权限矩阵） */
export type StaffRole = 'admin' | 'registrar' | 'teacher'

export const STAFF_ROLES: StaffRole[] = ['admin', 'registrar', 'teacher']

export const ROLE_LABELS: Record<StaffRole, string> = {
  admin: '机构管理员',
  registrar: '教务',
  teacher: '老师',
}

export function isStaffRole(value: unknown): value is StaffRole {
  return typeof value === 'string' && (STAFF_ROLES as string[]).includes(value)
}

/** 05 §1.1 登录响应 */
export interface LoginResult {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  role: StaffRole
  tenant_id: string | null
}

/** 05 §1.3 刷新响应 */
export interface RefreshResult {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

/** 本地登录态（刷新页面后从 localStorage 恢复） */
export interface AuthProfile {
  role: StaffRole
  tenantId: string | null
  account: string
}
