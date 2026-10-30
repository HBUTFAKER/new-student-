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
