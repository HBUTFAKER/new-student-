/**
 * request-core 单测（node:test，零依赖）。
 *
 * 跑法：node --test miniprogram/tests/
 * 这层不碰 wx.*，所以可以在 Node 里直连；wx 的适配在 utils/request.js，
 * 那条链路靠微信开发者工具人工验证（见 miniprogram/README.md）。
 */

'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')

const core = require('../utils/request-core')

const OLD_TOKEN = 'Bearer old-token'
const NEW_TOKEN = 'Bearer new-token'

/**
 * 造一个可观测的管线：记录每次请求、refresh 次数、清场次数、toast 文案。
 * onRequest 只接管业务请求；/auth/refresh 由 harness 统一处理。
 */
function makeHarness(options) {
  const opts = options || {}
  const state = {
    accessToken: opts.noAccessToken ? '' : 'old-token',
    refreshToken: opts.noRefreshToken ? '' : 'refresh-1',
    cleared: 0,
    expired: 0,
    toasts: [],
    refreshCalls: 0,
    businessCalls: 0,
    requests: [],
  }

  const pipeline = core.createRequestPipeline({
    baseUrl: 'http://api.test',
    getAccessToken: () => state.accessToken,
    getRefreshToken: () => state.refreshToken,
    saveTokens: (pair) => {
      state.accessToken = pair.access_token
      if (pair.refresh_token) state.refreshToken = pair.refresh_token
    },
    clearTokens: () => {
      state.cleared += 1
      state.accessToken = ''
      state.refreshToken = ''
    },
    onSessionExpired: () => {
      state.expired += 1
    },
    toast: (message) => {
      state.toasts.push(message)
    },
    send: async (request) => {
      state.requests.push(request)

      if (request.url === 'http://api.test/auth/refresh') {
        state.refreshCalls += 1
        if (opts.networkDown) throw new Error('socket hang up')
        if (opts.refreshFails) {
          return {
            statusCode: 401,
            data: { error: { code: 'INVALID_REFRESH', message: 'refresh 已失效' } },
          }
        }
        return {
          statusCode: 200,
          data: { access_token: 'new-token', refresh_token: 'refresh-2' },
        }
      }

      state.businessCalls += 1
      if (opts.networkDown) throw new Error('socket hang up')
      if (opts.onRequest) return opts.onRequest(request, state)
      return { statusCode: 200, data: { ok: true, auth: request.headers.Authorization } }
    },
  })

  return { pipeline, state }
}

test('buildHeaders：没有 token 时不拼 Authorization', () => {
  assert.deepEqual(core.buildHeaders(''), { 'Content-Type': 'application/json' })
  assert.deepEqual(core.buildHeaders(null), { 'Content-Type': 'application/json' })
  assert.equal(core.buildHeaders('abc').Authorization, 'Bearer abc')
  assert.deepEqual(core.buildHeaders(null, { 'X-Trace': '1' }), {
    'Content-Type': 'application/json',
    'X-Trace': '1',
  })
})

test('parseError：把平铺在 error 里的明细原样保留', () => {
  const parsed = core.parseError(409, {
    error: {
      code: 'SCHEDULE_CONFLICT',
      message: '时间冲突',
      trace_id: 'trace-1',
      conflicts: [{ type: 'teacher' }],
    },
  })

  assert.equal(parsed.code, 'SCHEDULE_CONFLICT')
  assert.equal(parsed.message, '时间冲突')
  assert.equal(parsed.status, 409)
  assert.deepEqual(parsed.detail, { conflicts: [{ type: 'teacher' }] })
})

test('parseError：非标准错误体回落到 HTTP_<status> + 默认文案', () => {
  const parsed = core.parseError(502, '<html>bad gateway</html>')
  assert.equal(parsed.code, 'HTTP_502')
  assert.equal(parsed.detail, null)
  assert.ok(parsed.message.length > 0)
})

test('正常请求：注入 Bearer 并原样返回 data', async () => {
  const { pipeline, state } = makeHarness()

  const result = await pipeline.get('/me/students')

  assert.equal(result.auth, OLD_TOKEN)
  assert.equal(state.requests[0].url, 'http://api.test/me/students')
  assert.equal(state.requests[0].method, 'GET')
  assert.equal(state.refreshCalls, 0)
})

test('并发 3 个 401 只发一次 refresh，三个请求都用新 token 重放成功', async () => {
  const seen = []

  const { pipeline, state } = makeHarness({
    onRequest: (request) => {
      seen.push(request.headers.Authorization)
      if (request.headers.Authorization === OLD_TOKEN) {
        return { statusCode: 401, data: { error: { code: 'TOKEN_EXPIRED', message: '过期' } } }
      }
      return { statusCode: 200, data: { token: request.headers.Authorization } }
    },
  })

  const results = await Promise.all([
    pipeline.get('/me/students'),
    pipeline.get('/me/students'),
    pipeline.get('/me/students'),
  ])

  assert.equal(state.refreshCalls, 1, '并发 401 只能触发一次 refresh')
  assert.equal(results.length, 3)
  for (const result of results) {
    assert.equal(result.token, NEW_TOKEN)
  }
  // 3 次原始请求 + 3 次重放
  assert.equal(state.businessCalls, 6)
  assert.equal(seen.filter((value) => value === OLD_TOKEN).length, 3)
  assert.equal(seen.filter((value) => value === NEW_TOKEN).length, 3)
  assert.equal(state.cleared, 0)
  assert.equal(state.expired, 0)
})

test('refresh 失败：清空登录态、只回调一次清场、三个请求都抛 SESSION_EXPIRED', async () => {
  const { pipeline, state } = makeHarness({
    refreshFails: true,
    onRequest: () => ({
      statusCode: 401,
      data: { error: { code: 'TOKEN_EXPIRED', message: '过期' } },
    }),
  })

  const results = await Promise.allSettled([
    pipeline.get('/me/students'),
    pipeline.get('/me/students'),
    pipeline.get('/me/students'),
  ])

  assert.ok(results.every((item) => item.status === 'rejected'))
  for (const item of results) {
    assert.equal(item.reason.code, core.SESSION_EXPIRED)
  }
  assert.equal(state.refreshCalls, 1)
  assert.equal(state.cleared, 1, '并发 3 个失败也只清一次场')
  assert.equal(state.expired, 1, 'reLaunch 只该发生一次')
})

test('续期后仍然 401：不无限重试，直接清场退出', async () => {
  const { pipeline, state } = makeHarness({
    onRequest: () => ({
      statusCode: 401,
      data: { error: { code: 'TOKEN_EXPIRED', message: '过期' } },
    }),
  })

  await assert.rejects(() => pipeline.get('/me/students'), (error) => {
    assert.equal(error.code, core.SESSION_EXPIRED)
    assert.equal(error.status, 401)
    return true
  })

  assert.equal(state.refreshCalls, 1)
  assert.equal(state.businessCalls, 2, '原始 + 重放各一次，不再多打')
  assert.equal(state.cleared, 1)
  assert.equal(state.expired, 1)
})

test('refresh token 本身不存在：不发 refresh 请求，直接清场', async () => {
  const { pipeline, state } = makeHarness({
    noRefreshToken: true,
    onRequest: () => ({
      statusCode: 401,
      data: { error: { code: 'TOKEN_EXPIRED', message: '过期' } },
    }),
  })

  await assert.rejects(() => pipeline.get('/me/students'), (error) => {
    assert.equal(error.code, core.SESSION_EXPIRED)
    return true
  })

  assert.equal(state.refreshCalls, 0)
  assert.equal(state.cleared, 1)
  assert.equal(state.expired, 1)
})

test('业务错误（422）：抛错带 code/status/detail，并 toast 后端文案', async () => {
  const { pipeline, state } = makeHarness({
    onRequest: () => ({
      statusCode: 422,
      data: {
        error: {
          code: 'VALIDATION_ERROR',
          message: 'wx_code: 长度不足',
          trace_id: 'trace-9',
          field: 'wx_code',
        },
      },
    }),
  })

  await assert.rejects(() => pipeline.post('/auth/wechat-login', { wx_code: '' }), (error) => {
    assert.equal(error.code, 'VALIDATION_ERROR')
    assert.equal(error.status, 422)
    assert.equal(error.message, 'wx_code: 长度不足')
    assert.deepEqual(error.detail, { field: 'wx_code' })
    return true
  })

  assert.deepEqual(state.toasts, ['wx_code: 长度不足'])
})

test('silent: true 时不弹 toast，错误照样抛给调用方', async () => {
  const { pipeline, state } = makeHarness({
    onRequest: () => ({
      statusCode: 403,
      data: { error: { code: 'OUT_OF_SCOPE', message: '仅家长可访问' } },
    }),
  })

  await assert.rejects(() => pipeline.get('/me/students', { silent: true }), (error) => {
    assert.equal(error.code, 'OUT_OF_SCOPE')
    return true
  })

  assert.deepEqual(state.toasts, [])
})

test('登录接口自身 401：不触发 refresh，当业务错误抛出', async () => {
  const { pipeline, state } = makeHarness({
    onRequest: () => ({
      statusCode: 401,
      data: { error: { code: 'WECHAT_CODE_INVALID', message: 'code 无效' } },
    }),
  })

  await assert.rejects(
    () => pipeline.post('/auth/wechat-login', { wx_code: 'x' }, { skipAuth: true, skipRefresh: true }),
    (error) => {
      assert.equal(error.code, 'WECHAT_CODE_INVALID')
      return true
    }
  )

  assert.equal(state.refreshCalls, 0)
  assert.equal(state.expired, 0)
})

test('网络层失败：包成 NETWORK_ERROR，不重试、可读文案', async () => {
  const { pipeline, state } = makeHarness({ networkDown: true })

  await assert.rejects(() => pipeline.get('/me/students'), (error) => {
    assert.equal(error.code, core.NETWORK_ERROR)
    assert.equal(error.status, 0)
    assert.ok(error.message.includes('网络'))
    return true
  })

  assert.equal(state.businessCalls, 1)
  assert.equal(state.refreshCalls, 0)
  assert.equal(state.toasts.length, 1)
})

test('无 token 时不带 Authorization 头', async () => {
  const { pipeline, state } = makeHarness({ noAccessToken: true })

  await pipeline.get('/me/students')

  assert.equal(state.requests[0].headers.Authorization, undefined)
})

test('resetSession 之后清场逻辑可以再次触发', async () => {
  const { pipeline, state } = makeHarness({
    refreshFails: true,
    onRequest: () => ({
      statusCode: 401,
      data: { error: { code: 'TOKEN_EXPIRED', message: '过期' } },
    }),
  })

  await assert.rejects(() => pipeline.get('/me/students'), (error) => {
    assert.equal(error.code, core.SESSION_EXPIRED)
    return true
  })
  assert.equal(state.expired, 1)

  // 模拟清场后重新登录：storage 里重新有了 token，会话重建
  state.accessToken = 'fresh-token'
  state.refreshToken = 'refresh-9'
  pipeline.resetSession()

  await assert.rejects(() => pipeline.get('/me/students'), (error) => {
    assert.equal(error.code, core.SESSION_EXPIRED)
    return true
  })

  assert.equal(state.expired, 2, 'resetSession 之后清场回调应能再次触发')
  assert.equal(state.cleared, 2)
})

test('createSingleFlight：并发只执行一次，结束后可再次执行', async () => {
  let calls = 0
  const run = core.createSingleFlight(async () => {
    calls += 1
    return calls
  })

  const [a, b, c] = await Promise.all([run(), run(), run()])
  assert.equal(calls, 1)
  assert.equal(a, 1)
  assert.equal(b, 1)
  assert.equal(c, 1)

  const again = await run()
  assert.equal(calls, 2)
  assert.equal(again, 2)
})
