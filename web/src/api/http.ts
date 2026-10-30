import axios, { AxiosError } from 'axios'
import type { AxiosRequestConfig, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'

import { clearTokens, getAccessToken, getRefreshToken, setTokens } from './tokens'
import type { RefreshResult } from '@/types/auth'

declare module 'axios' {
  export interface AxiosRequestConfig {
    /** 为 true 时拦截器不弹 ElMessage，交由调用方自己展示（如登录页内联报错） */
    skipErrorToast?: boolean
    /** 内部标记：该请求已因 401 重放过一次，避免无限循环 */
    _retried?: boolean
  }
}

export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? ''

/** 后端统一错误体（05 §0）：{ error: { code, message, trace_id, ...detail } } */
interface ErrorEnvelope {
  error?: {
    code?: string
    message?: string
    trace_id?: string
    [key: string]: unknown
  }
}

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  /** FastAPI 422 校验错误按字段归位：{ name: '...', capacity: '...' }，供表单一格一格回显 */
  readonly fieldErrors?: Record<string, string>
  /** 业务错误附带的明细，原样透传（如 409 调课冲突的 conflicts 数组） */
  readonly detail?: Record<string, unknown>

  constructor(
    message: string,
    code: string,
    status: number,
    fieldErrors?: Record<string, string>,
    detail?: Record<string, unknown>,
  ) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.fieldErrors = fieldErrors
    this.detail = detail
  }

  /** 调课/加课冲突明细（3.3 ConflictOut），没有就返回空数组 */
  conflicts(): unknown[] {
    const value = this.detail?.conflicts
    return Array.isArray(value) ? value : []
  }
}

export const http = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15000,
})

let sessionExpiredHandler: (() => void) | null = null

/** 登录态彻底失效（refresh 也过期）时回调，由 main.ts 接上「清状态 + 回登录页」 */
export function onSessionExpired(handler: () => void): void {
  sessionExpiredHandler = handler
}

http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getAccessToken()
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`)
  }
  return config
})

/** 单航班：并发多个 401 只发一次 refresh，其余请求等同一个 Promise */
let refreshing: Promise<string> | null = null

async function refreshAccessToken(): Promise<string> {
  if (!refreshing) {
    refreshing = (async () => {
      const refreshToken = getRefreshToken()
      if (!refreshToken) {
        throw new ApiError('登录状态已失效，请重新登录', 'TOKEN_MISSING', 401)
      }
      const response = await axios.post<RefreshResult>(
        `${API_BASE_URL}/auth/refresh`,
        { refresh_token: refreshToken },
        { timeout: 15000 },
      )
      setTokens(response.data.access_token, response.data.refresh_token)
      return response.data.access_token
    })().finally(() => {
      refreshing = null
    })
  }
  return refreshing
}

function isAuthEndpoint(url: string | undefined): boolean {
  if (!url) return false
  return url.includes('/auth/login') || url.includes('/auth/refresh')
}

function defaultMessage(status: number): string {
  if (status === 0) return '无法连接服务器，请确认后端服务已启动'
  if (status === 400) return '请求参数有误'
  if (status === 401) return '登录状态已失效，请重新登录'
  if (status === 403) return '没有权限执行该操作'
  if (status === 404) return '请求的资源不存在'
  if (status === 409) return '数据冲突，请刷新后重试'
  if (status === 422) return '提交的数据不合法'
  if (status >= 500) return '服务器异常，请稍后重试'
  return '请求失败，请稍后重试'
}

interface ValidationIssue {
  loc?: (string | number)[]
  msg?: string
}

/** FastAPI 422 的原始体是 { detail: [{ loc: ["body","name"], msg }] }，不是我们的错误信封 */
function extractFieldErrors(body: unknown): Record<string, string> | undefined {
  if (!body || typeof body !== 'object') return undefined
  const detail = (body as { detail?: unknown }).detail
  if (!Array.isArray(detail)) return undefined

  const fieldErrors: Record<string, string> = {}
  for (const item of detail as ValidationIssue[]) {
    const path = (item.loc ?? []).slice(1).join('.')
    if (path && typeof item.msg === 'string' && !fieldErrors[path]) {
      fieldErrors[path] = item.msg
    }
  }
  return Object.keys(fieldErrors).length > 0 ? fieldErrors : undefined
}

/**
 * 业务错误自带的明细（如 409 调课冲突的 `conflicts`、`SCHEDULE_CONFLICT` 的明细）。
 * 后端把 detail 平铺在 `error` 里，这里剥掉三个固定字段后原样保留。
 */
function extractDetail(body: unknown): Record<string, unknown> | undefined {
  if (!body || typeof body !== 'object') return undefined
  const envelope = (body as ErrorEnvelope).error
  if (!envelope || typeof envelope !== 'object') return undefined

  const detail: Record<string, unknown> = { ...envelope }
  delete detail.code
  delete detail.message
  delete detail.trace_id
  return Object.keys(detail).length > 0 ? detail : undefined
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error

  if (axios.isAxiosError<ErrorEnvelope>(error)) {
    const status = error.response?.status ?? 0
    const body = error.response?.data
    const fieldErrors = extractFieldErrors(body)
    const code = body?.error?.code ?? (status === 0 ? 'NETWORK_ERROR' : `HTTP_${status}`)
    const message = body?.error?.message ?? defaultMessage(status)
    return new ApiError(message, code, status, fieldErrors, extractDetail(body))
  }

  return new ApiError('请求失败，请稍后重试', 'UNKNOWN_ERROR', 0)
}

http.interceptors.response.use(
  (response: AxiosResponse) => response,
  async (error: AxiosError<ErrorEnvelope>) => {
    const config: AxiosRequestConfig | undefined = error.config
    const status = error.response?.status

    if (status === 401 && config && !isAuthEndpoint(config.url)) {
      if (!config._retried) {
        config._retried = true
        try {
          const token = await refreshAccessToken()
          config.headers = { ...config.headers, Authorization: `Bearer ${token}` }
          return await http.request(config)
        } catch {
          clearTokens()
          sessionExpiredHandler?.()
          throw new ApiError('登录已过期，请重新登录', 'SESSION_EXPIRED', 401)
        }
      }
      // 续期后仍 401：refresh token 本身已失效，直接踢回登录页
      clearTokens()
      sessionExpiredHandler?.()
      throw new ApiError('登录已过期，请重新登录', 'SESSION_EXPIRED', 401)
    }

    const apiError = toApiError(error)
    if (!config?.skipErrorToast) {
      ElMessage.error(apiError.message)
    }
    throw apiError
  },
)
