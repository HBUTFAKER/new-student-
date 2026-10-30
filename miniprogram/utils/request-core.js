/**
 * 请求管线的纯逻辑层（不碰 wx.*，可在 Node 里直接单测）。
 *
 * 设计要点
 * - 所有平台能力（发请求、读写 storage、toast、跳转）由外部注入，本文件只做调度。
 * - 401 续期是「单航班」：并发多个 401 只发一次 /auth/refresh，其余请求等同一个
 *   Promise；refresh 失败则清场并回调 onSessionExpired，不无限重试。
 * - 业务错误统一抛 ApiError，带 code / status / detail，供页面分支处理。
 */

'use strict'

/** 后端统一错误体（05 §0）：{ error: { code, message, trace_id, ...detail } } */
var NETWORK_ERROR = 'NETWORK_ERROR'
var SESSION_EXPIRED = 'SESSION_EXPIRED'
var TOKEN_MISSING = 'TOKEN_MISSING'

function ApiError(message, code, status, detail) {
  var error = new Error(message)
  error.name = 'ApiError'
  error.code = code
  error.status = typeof status === 'number' ? status : 0
  error.detail = detail || null
  return error
}

/**
 * 组请求头：有 token 才带 Authorization，避免无意义地把 null 拼成字符串。
 * 第二个参数用于覆盖（如续期后显式带入新 token）。
 */
function buildHeaders(accessToken, extraHeaders) {
  var headers = Object.assign({ 'Content-Type': 'application/json' }, extraHeaders || {})
  if (accessToken) {
    headers.Authorization = 'Bearer ' + accessToken
  }
  return headers
}

function defaultMessage(status) {
  if (status === 0) return '网络连接失败，请检查网络或服务地址'
  if (status === 400) return '请求参数有误'
  if (status === 401) return '登录已失效，请重新登录'
  if (status === 403) return '没有权限查看该内容'
  if (status === 404) return '请求的资源不存在'
  if (status === 409) return '数据冲突，请刷新后重试'
  if (status === 422) return '提交的数据不合法'
  if (status >= 500) return '服务器开小差了，请稍后重试'
  return '请求失败，请稍后重试'
}

/**
 * 解析后端错误体。后端把 detail 平铺进 error，这里剥掉三个固定字段后原样保留，
 * 页面可拿 detail.conflicts 之类的明细，不必再解析原始响应。
 */
function parseError(status, body) {
  var envelope = body && typeof body === 'object' ? body.error : null
  if (!envelope || typeof envelope !== 'object') {
    return {
      code: status === 0 ? NETWORK_ERROR : 'HTTP_' + status,
      message: defaultMessage(status),
      status: status,
      detail: null,
    }
  }

  var detail = Object.assign({}, envelope)
  delete detail.code
  delete detail.message
  delete detail.trace_id

  return {
    code: envelope.code || 'HTTP_' + status,
    message: envelope.message || defaultMessage(status),
    status: status,
    detail: Object.keys(detail).length > 0 ? detail : null,
  }
}

/**
 * 单航班：并发调用只执行一次，全部等同一个 Promise。
 * 结束后（无论成败）复位，下一次再触发才会重新执行。
 */
function createSingleFlight(task) {
  var pending = null

  return function run() {
    if (pending) return pending

    var current = Promise.resolve().then(task)
    pending = current

    function reset() {
      // 只复位自己这一轮，避免晚到的旧回调把新的一轮误清
      if (pending === current) pending = null
    }
    current.then(reset, reset)

    return current
  }
}

/**
 * @param {object} deps
 * @param {string} deps.baseUrl
 * @param {number} [deps.timeout]
 * @param {() => string} deps.getAccessToken
 * @param {() => string} deps.getRefreshToken
 * @param {(pair: {access_token: string, refresh_token?: string}) => void} deps.saveTokens
 * @param {() => void} deps.clearTokens
 * @param {() => void} deps.onSessionExpired
 * @param {(message: string) => void} deps.toast
 * @param {(options: object) => Promise<{statusCode: number, data: any}>} deps.send 裸传输层，网络异常时 reject
 */
function createRequestPipeline(deps) {
  var baseUrl = (deps.baseUrl || '').replace(/\/+$/, '')
  var timeout = deps.timeout || 15000

  // 清场只做一次：并发 3 个 401 同时因 refresh 失败而挂掉，不应连发 3 次 reLaunch
  var expiredHandled = false

  function resetSession() {
    expiredHandled = false
  }

  function handleSessionExpired() {
    if (expiredHandled) return
    expiredHandled = true
    deps.clearTokens()
    deps.onSessionExpired()
  }

  var refreshOnce = createSingleFlight(function () {
    var refreshToken = deps.getRefreshToken()
    if (!refreshToken) {
      return Promise.reject(ApiError('登录状态已失效，请重新登录', TOKEN_MISSING, 401))
    }

    return deps
      .send({
        url: baseUrl + '/auth/refresh',
        method: 'POST',
        data: { refresh_token: refreshToken },
        headers: buildHeaders(null),
        timeout: timeout,
      })
      .then(function (response) {
        var data = response && response.data
        if (!data || !data.access_token) {
          return Promise.reject(ApiError('续期失败', SESSION_EXPIRED, response ? response.statusCode : 401))
        }
        deps.saveTokens({
          access_token: data.access_token,
          refresh_token: data.refresh_token || refreshToken,
        })
        return data.access_token
      })
  })

  function resolveUrl(url) {
    if (/^https?:\/\//.test(url)) return url
    return baseUrl + (url.charAt(0) === '/' ? url : '/' + url)
  }

  function run(options, accessTokenOverride, isRetry) {
    var skipAuth = options.skipAuth === true

    var requestOptions = {
      url: resolveUrl(options.url),
      method: options.method || 'GET',
      data: options.data,
      headers: buildHeaders(
        skipAuth ? null : accessTokenOverride || deps.getAccessToken(),
        options.headers
      ),
      timeout: options.timeout || timeout,
    }

    return deps.send(requestOptions).then(
      function (response) {
        var status = response.statusCode

        if (status >= 200 && status < 300) {
          return response.data
        }

        // 401：只有「带身份的业务请求」才续期，登录/刷新本身 401 直接当业务错误抛
        if (status === 401 && !skipAuth && options.skipRefresh !== true) {
          if (isRetry) {
            // 续期后仍然 401，说明 refresh token 也废了，清场退出，不再重试
            handleSessionExpired()
            return Promise.reject(ApiError('登录已失效，请重新登录', SESSION_EXPIRED, 401))
          }

          return refreshOnce().then(
            function (token) {
              return run(options, token, true)
            },
            function () {
              handleSessionExpired()
              return Promise.reject(ApiError('登录已失效，请重新登录', SESSION_EXPIRED, 401))
            }
          )
        }

        var parsed = parseError(status, response.data)
        var apiError = ApiError(parsed.message, parsed.code, parsed.status, parsed.detail)
        if (options.silent !== true) {
          deps.toast(parsed.message)
        }
        return Promise.reject(apiError)
      },
      function () {
        // 传输层失败（断网、域名未配置、超时）——不重试，直接给出可读报错
        var networkError = ApiError(defaultMessage(0), NETWORK_ERROR, 0)
        if (options.silent !== true) {
          deps.toast(networkError.message)
        }
        return Promise.reject(networkError)
      }
    )
  }

  function request(options) {
    return run(options, null, false)
  }

  return {
    request: request,
    get: function (url, options) {
      return request(Object.assign({}, options || {}, { url: url, method: 'GET' }))
    },
    post: function (url, data, options) {
      return request(Object.assign({}, options || {}, { url: url, method: 'POST', data: data }))
    },
    patch: function (url, data, options) {
      return request(Object.assign({}, options || {}, { url: url, method: 'PATCH', data: data }))
    },
    delete: function (url, options) {
      return request(Object.assign({}, options || {}, { url: url, method: 'DELETE' }))
    },
    /** 单测与登录成功后复位用：重新建立会话后，401 清场逻辑可以再次生效 */
    resetSession: resetSession,
    /** 暴露给单测断言「只发一次」 */
    _refreshOnce: refreshOnce,
  }
}

module.exports = {
  ApiError: ApiError,
  NETWORK_ERROR: NETWORK_ERROR,
  SESSION_EXPIRED: SESSION_EXPIRED,
  TOKEN_MISSING: TOKEN_MISSING,
  buildHeaders: buildHeaders,
  parseError: parseError,
  defaultMessage: defaultMessage,
  createSingleFlight: createSingleFlight,
  createRequestPipeline: createRequestPipeline,
}
