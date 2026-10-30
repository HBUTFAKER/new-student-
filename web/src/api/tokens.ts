/**
 * token 存取：内存一份 + localStorage 一份。
 * 内存负责请求链路（不碰磁盘），localStorage 负责刷新页面不丢登录态。
 */
const ACCESS_KEY = 'wb.access_token'
const REFRESH_KEY = 'wb.refresh_token'

let accessToken: string | null = null
let refreshToken: string | null = null

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStorage(key: string, value: string | null): void {
  try {
    if (value === null) {
      window.localStorage.removeItem(key)
    } else {
      window.localStorage.setItem(key, value)
    }
  } catch {
    // localStorage 不可用（隐私模式等）时只保留内存态，不阻断登录
  }
}

export function getAccessToken(): string | null {
  if (accessToken === null) {
    accessToken = readStorage(ACCESS_KEY)
  }
  return accessToken
}

export function getRefreshToken(): string | null {
  if (refreshToken === null) {
    refreshToken = readStorage(REFRESH_KEY)
  }
  return refreshToken
}

export function setTokens(access: string, refresh: string): void {
  accessToken = access
  refreshToken = refresh
  writeStorage(ACCESS_KEY, access)
  writeStorage(REFRESH_KEY, refresh)
}

export function clearTokens(): void {
  accessToken = null
  refreshToken = null
  writeStorage(ACCESS_KEY, null)
  writeStorage(REFRESH_KEY, null)
}
