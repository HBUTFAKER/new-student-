import { http } from './http'
import type { LoginResult } from '@/types/auth'

export interface LoginPayload {
  phone: string
  password: string
}

/** 05 §1.1 员工账密登录 */
export async function login(payload: LoginPayload): Promise<LoginResult> {
  const { data } = await http.post<LoginResult>('/auth/login', payload, { skipErrorToast: true })
  return data
}

export interface RegisterPayload {
  org_name: string
  admin_name: string
  admin_phone: string
  admin_password: string
}

export interface RegisterResult {
  tenant_id: string
  name: string
  admin_phone: string
  plan_id: string
  trial_end_date: string
}

/** 7.1 公开自助注册（无 token，注册完走登录页） */
export async function register(payload: RegisterPayload): Promise<RegisterResult> {
  const { data } = await http.post<RegisterResult>('/platform/register', payload, {
    skipErrorToast: true,
  })
  return data
}
