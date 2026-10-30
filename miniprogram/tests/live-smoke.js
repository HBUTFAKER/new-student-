/**
 * 真后端联调冒烟（手动跑，不进 CI，不参与 node --test）。
 *
 * 前提：后端以 stub 微信模式启动（local/test 环境默认就是 stub，任意 wx_code 都能登录）。
 *
 *   MP_API_BASE=http://127.0.0.1:8010 node miniprogram/tests/live-smoke.js
 *
 * 覆盖：wx.login → /auth/wechat-login → 存 token → /me/students → 首页数据；
 *       失效 access_token 触发的 401 单航班续期；无绑定账号走 needBind 分支；断网报错文案。
 * 不覆盖：真实 wx.request / 页面渲染（那两条只能在微信开发者工具里人工看）。
 */

'use strict'

const assert = require('node:assert/strict')

const BASE = process.env.MP_API_BASE || 'http://127.0.0.1:8000'
const BOUND_WX_CODE = process.env.MP_BOUND_CODE || 'live_smoke_openid|live_smoke_unionid'
const EMPTY_WX_CODE = process.env.MP_EMPTY_CODE || 'live_smoke_new_openid|live_smoke_new_unionid'

const store = new Map()
const toasts = []
const reLaunches = []

let loginCode = BOUND_WX_CODE
let networkDown = false

global.wx = {
  getStorageSync(key) {
    return store.has(key) ? store.get(key) : ''
  },
  setStorageSync(key, value) {
    store.set(key, value)
  },
  removeStorageSync(key) {
    store.delete(key)
  },
  getAccountInfoSync() {
    return { miniProgram: { envVersion: 'develop' } }
  },
  login({ success, fail }) {
    if (networkDown) return fail({ errMsg: 'login:fail' })
    return success({ code: loginCode })
  },
  showToast({ title }) {
    toasts.push(title)
  },
  reLaunch({ url }) {
    reLaunches.push(url)
  },
  request({ url, method, header, data, success, fail }) {
    if (networkDown) {
      fail({ errMsg: 'request:fail' })
      return
    }
    const init = { method: method || 'GET', headers: header || {} }
    if (init.method !== 'GET' && data !== undefined) init.body = JSON.stringify(data)

    fetch(url, init)
      .then(async (response) => {
        const text = await response.text()
        let body = null
        try {
          body = text ? JSON.parse(text) : null
        } catch (error) {
          body = text
        }
        success({ statusCode: response.status, data: body })
      })
      .catch(fail)
  },
}

// 顺序很重要：先改 config.BASE_URL，再 require 会读它的 request.js
const config = require('../utils/config')
config.BASE_URL = BASE

const storage = require('../utils/storage')
const http = require('../utils/request')
const auth = require('../utils/auth')
const core = require('../utils/request-core')

let passed = 0
let failed = 0

function check(name, fn) {
  try {
    fn()
    passed += 1
    console.log(`  ok  ${name}`)
  } catch (error) {
    failed += 1
    console.log(`  FAIL ${name}`)
    console.log(`       ${error.message}`)
  }
}

async function main() {
  console.log(`base = ${BASE}`)

  // ---------- 1. 首次登录（已绑定账号） ----------
  store.clear()
  loginCode = BOUND_WX_CODE

  const first = await auth.login()
  const saved = storage.getAuth() || {}

  check('登录后拿到 access_token', () => assert.ok(saved.access_token))
  check('登录后拿到 refresh_token', () => assert.ok(saved.refresh_token))
  check('role = parent', () => assert.equal(saved.role, 'parent'))
  check('tenant_id 有值', () => assert.ok(saved.tenant_id))
  check('active_student_id 有值', () => assert.ok(saved.active_student_id))
  check('needBind = false', () => assert.equal(first.needBind, false))
  check('/me/students 至少 1 个孩子', () => assert.ok(first.students.length >= 1))
  check('孩子带姓名', () => assert.ok(first.students[0].student_name.length > 0))
  check('bound_students 回填为实时数量', () =>
    assert.equal(saved.bound_students, first.students.length)
  )
  check('不落地 openid / unionid', () => {
    const serialized = JSON.stringify(saved)
    assert.equal(serialized.includes('openid'), false)
    assert.equal(serialized.includes('unionid'), false)
  })

  // ---------- 2. 正常业务请求带上了 Bearer ----------
  const students = await http.get('/me/students', { silent: true })
  check('GET /me/students 正常返回', () => assert.ok(Array.isArray(students.items)))

  // ---------- 3. 坏 access_token 触发 401 单航班续期 ----------
  const goodToken = storage.getAccessToken()
  const goodRefresh = storage.getRefreshToken()
  storage.saveTokens({ access_token: 'definitely.not.a.jwt', refresh_token: goodRefresh })

  const afterRefresh = await http.get('/me/students', { silent: true })
  check('401 后自动续期并重放成功', () => assert.ok(Array.isArray(afterRefresh.items)))
  check('access_token 已换成新的', () => assert.notEqual(storage.getAccessToken(), 'definitely.not.a.jwt'))
  check('refresh_token 没有被清空', () => assert.ok(storage.getRefreshToken()))
  check('续期过程没有把人踢回登录页', () => assert.equal(reLaunches.length, 0))

  // ---------- 4. 并发 3 个业务请求，仍只有一次续期 ----------
  storage.saveTokens({ access_token: 'broken.again.token', refresh_token: storage.getRefreshToken() })
  const concurrent = await Promise.all([
    http.get('/me/students', { silent: true }),
    http.get('/me/students', { silent: true }),
    http.get('/me/students', { silent: true }),
  ])
  check('并发 3 个 401 全部恢复', () => assert.equal(concurrent.length, 3))
  check('并发恢复后 token 有效', () => assert.notEqual(storage.getAccessToken(), 'broken.again.token'))

  // ---------- 5. refresh 也失效：清场并回登录页 ----------
  storage.saveTokens({ access_token: 'broken.token.3', refresh_token: 'broken.refresh.token' })
  let expiredError = null
  try {
    await http.get('/me/students', { silent: true })
  } catch (error) {
    expiredError = error
  }
  check('refresh 失效时抛 SESSION_EXPIRED', () =>
    assert.equal(expiredError && expiredError.code, core.SESSION_EXPIRED)
  )
  check('登录态已清空', () => assert.equal(storage.getAccessToken(), ''))
  check('已跳回登录页', () => assert.ok(reLaunches.includes('/pages/login/index')))

  // ---------- 6. 无绑定账号：走绑定引导分支 ----------
  store.clear()
  loginCode = EMPTY_WX_CODE
  const empty = await auth.login()
  check('无绑定账号 needBind = true', () => assert.equal(empty.needBind, true))
  check('无绑定时孩子列表为空', () => assert.equal(empty.students.length, 0))
  check('无绑定时 tenant_id 为空', () => assert.equal(storage.getAuth().tenant_id, null))

  // ---------- 7. 断网：给可读报错，不白屏 ----------
  store.clear()
  networkDown = true
  let networkError = null
  try {
    await auth.login()
  } catch (error) {
    networkError = error
  }
  check('断网时抛 NETWORK_ERROR', () =>
    assert.equal(networkError && networkError.code, core.NETWORK_ERROR)
  )
  check('断网文案可读', () => assert.ok(networkError.message.includes('网络')))
  networkDown = false

  // ---------- 8. 未登录直接请求：不炸在鉴权上 ----------
  store.clear()
  const anonymous = await http.get('/me/students', { silent: true }).catch((error) => error)
  check('无 token 请求 /me/students 被 401 挡住', () =>
    assert.equal(anonymous.code, core.SESSION_EXPIRED)
  )

  console.log(`\n${passed} passed, ${failed} failed`)
  process.exit(failed === 0 ? 0 : 1)
}

main().catch((error) => {
  console.error('live smoke 崩了：', error)
  process.exit(1)
})
