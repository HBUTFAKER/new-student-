import { http } from './http'
import type { Paged } from '@/types/archive'
import type {
  PlanItem,
  RenewPayload,
  RenewResult,
  TenantCreatePayload,
  TenantCreateResult,
  TenantDetail,
  TenantListItem,
} from '@/types/platform'

/** 租户列表（7.4）：name 模糊搜索 + 分页，后端按 end_date 升序 */
export function listTenants(params: {
  name?: string
  page?: number
  page_size?: number
}): Promise<Paged<TenantListItem>> {
  return http.get<Paged<TenantListItem>>('/platform/tenants', { params }).then((r) => r.data)
}

/** 租户详情（7.4） */
export function getTenant(id: string): Promise<TenantDetail> {
  return http.get<TenantDetail>(`/platform/tenants/${id}`).then((r) => r.data)
}

/** 套餐列表（续费弹窗换套餐下拉） */
export function listPlans(): Promise<PlanItem[]> {
  return http.get<{ items: PlanItem[] }>('/platform/plans').then((r) => r.data.items)
}

/** 手动续费（7.2 接口复用） */
export function renewTenant(id: string, payload: RenewPayload): Promise<RenewResult> {
  return http.post<RenewResult>(`/platform/tenants/${id}/renew`, payload).then((r) => r.data)
}

/** 手动开通机构（复用 POST /platform/tenants） */
export function createTenant(payload: TenantCreatePayload): Promise<TenantCreateResult> {
  return http.post<TenantCreateResult>('/platform/tenants', payload).then((r) => r.data)
}
