import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { login as loginApi } from '@/api/auth'
import type { LoginPayload } from '@/api/auth'
import { ApiError } from '@/api/http'
import { clearTokens, getAccessToken, getRefreshToken, setTokens } from '@/api/tokens'
import { isStaffRole } from '@/types/auth'
import type { AuthProfile, StaffRole } from '@/types/auth'

const PROFILE_KEY = 'wb.profile'

function readProfile(): AuthProfile | null {
  try {
    const raw = window.localStorage.getItem(PROFILE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<AuthProfile>
    if (!isStaffRole(parsed.role)) return null
    return {
      role: parsed.role,
      tenantId: typeof parsed.tenantId === 'string' ? parsed.tenantId : null,
      account: typeof parsed.account === 'string' ? parsed.account : '',
    }
  } catch {
    return null
  }
}

function writeProfile(profile: AuthProfile | null): void {
  try {
    if (profile === null) {
      window.localStorage.removeItem(PROFILE_KEY)
    } else {
      window.localStorage.setItem(PROFILE_KEY, JSON.stringify(profile))
    }
  } catch {
    // 忽略存储异常，内存态仍可用
  }
}

export const useAuthStore = defineStore('auth', () => {
  const profile = ref<AuthProfile | null>(readProfile())
  const authenticated = ref(Boolean(getAccessToken() && getRefreshToken() && profile.value))

  const role = computed<StaffRole | null>(() => profile.value?.role ?? null)
  const account = computed<string>(() => profile.value?.account ?? '')
  const isAuthenticated = computed<boolean>(() => authenticated.value && profile.value !== null)

  async function signIn(payload: LoginPayload): Promise<StaffRole> {
    const result = await loginApi(payload)

    if (!isStaffRole(result.role)) {
      throw new ApiError('该账号不是员工账号，请使用家长端小程序登录', 'NOT_STAFF_ACCOUNT', 403)
    }

    setTokens(result.access_token, result.refresh_token)
    const nextProfile: AuthProfile = {
      role: result.role,
      tenantId: result.tenant_id,
      account: payload.phone,
    }
    profile.value = nextProfile
    authenticated.value = true
    writeProfile(nextProfile)
    return result.role
  }

  function signOut(): void {
    clearTokens()
    profile.value = null
    authenticated.value = false
    writeProfile(null)
  }

  return { profile, role, account, isAuthenticated, signIn, signOut }
})
