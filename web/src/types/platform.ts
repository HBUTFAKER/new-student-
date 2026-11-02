/** 平台后台（7.4）：租户列表 / 详情 / 套餐 / 手动开通 / 续费 */

export type SubStatus = 'expired' | 'trial' | 'active'

export interface TenantListItem {
  tenant_id: string
  name: string
  created_at: string
  plan_name: string
  plan_id: string
  start_date: string
  end_date: string
  days_left: number
  sub_status: SubStatus
  students_used: number
  messages_used: number
  period: string
}

export interface TenantDetail extends TenantListItem {
  max_students: number
  max_messages: number
  admin_name: string | null
  admin_phone: string | null
}

export interface PlanItem {
  id: string
  name: string
  max_students: number
  max_messages: number
}

export interface TenantCreatePayload {
  name: string
  admin_name: string
  admin_phone: string
  admin_password: string
}

export interface TenantCreateResult {
  tenant_id: string
  name: string
  admin_staff_id: string
  admin_phone: string
  plan_id: string
}

export interface RenewPayload {
  days: number
  plan_id?: string | null
}

export interface RenewResult {
  end_date: string
  plan_id: string
  plan_name: string
}
