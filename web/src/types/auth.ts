/** 机构内角色（机构 admin 可创建/管理；platform_admin 由平台侧 seed 创建，不在其列） */
export type TenantRole = 'admin' | 'registrar' | 'teacher'

/** 员工角色，与后端 JWT 的 role 取值一致（0.2 权限矩阵 + 平台管理员） */
export type StaffRole = TenantRole | 'platform_admin'

/** 机构内角色（新建员工账号单选、机构菜单可见性用） */
export const STAFF_ROLES: TenantRole[] = ['admin', 'registrar', 'teacher']

/** 全部员工角色（登录识别用：platform_admin 也是员工账号，登录不能拦） */
export const ALL_STAFF_ROLES: StaffRole[] = [...STAFF_ROLES, 'platform_admin']

export const ROLE_LABELS: Record<StaffRole, string> = {
  admin: '机构管理员',
  registrar: '教务',
  teacher: '老师',
  platform_admin: '平台管理员',
}

export function isStaffRole(value: unknown): value is StaffRole {
  return typeof value === 'string' && (ALL_STAFF_ROLES as string[]).includes(value)
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
