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
const session = require('../utils/session')

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

  // 教务 token 只放局部变量，不写 storage —— 同一个 pipeline 共用 storage，写进去会串身份
  const adminToken = (
    await http.post(
      '/auth/login',
      {
        phone: process.env.MP_ADMIN_PHONE || '13000000001',
        password: process.env.MP_ADMIN_PASSWORD || 'demo123456',
      },
      { skipAuth: true, skipRefresh: true, silent: true }
    )
  ).access_token

  function asAdmin(method, url, data) {
    const options = {
      skipAuth: true,
      skipRefresh: true,
      silent: true,
      headers: { Authorization: 'Bearer ' + adminToken },
    }
    return method === 'GET' ? http.get(url, options) : http.post(url, data, options)
  }

  /** 保证库里有 count 个在读学生；不够就用教务接口现场建，脚本不依赖特定种子数据 */
  async function ensureStudents(count) {
    let list = ((await asAdmin('GET', '/students?page_size=100&status=active')).items) || []
    let guard = 0
    while (list.length < count && guard < 5) {
      guard += 1
      await asAdmin('POST', '/students', { name: `冒烟学生${list.length + 1}` })
      list = ((await asAdmin('GET', '/students?page_size=100&status=active')).items) || []
    }
    return list
  }

  // ---------- 0. 前置：让「已绑定账号」真的有绑定（换库后第一次跑要现场造） ----------
  const seeded = await ensureStudents(2)
  if (seeded.length < 2) {
    console.log('建不出在读学生，先检查教务账号权限')
    report()
    return
  }

  store.clear()
  loginCode = BOUND_WX_CODE
  let first = await auth.login()

  if (first.students.length === 0) {
    console.log('  起始身份还没绑定，现场发码绑一个')
    const bootstrapCode = await asAdmin('POST', '/invite-codes', {
      student_id: seeded[0].id,
      expires_in_hours: 24,
    })
    await auth.bindChild(bootstrapCode.code)
    first = await auth.login()
  }

  // ---------- 1. 首次登录（已绑定账号） ----------
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

  // ---------- 9. 邀请码：教务发码 → 家长绑定 ----------
  const rosterItems = await ensureStudents(2)
  check('教务能拉到在读学生', () => assert.ok(rosterItems.length >= 2))

  const studentA = rosterItems[0]
  const studentB = rosterItems[1]

  async function issueCode(studentId, hours) {
    const created = await asAdmin('POST', '/invite-codes', {
      student_id: studentId,
      expires_in_hours: hours || 24,
    })
    return created.code
  }

  const codeForFirst = await issueCode(studentA.id, 24)
  check('生成的码是 AB12-CD34 形状', () => assert.match(codeForFirst, /^[0-9A-Z]{4}-[0-9A-Z]{4}$/))

  // 用一个从没登录过的微信身份，模拟「新家长」
  const stamp = String(Date.now()).slice(-7)
  store.clear()
  loginCode = `smoke_${stamp}_openid|smoke_${stamp}_unionid`
  const newParent = await auth.login()
  check('新微信号登录后 needBind = true', () => assert.equal(newParent.needBind, true))
  check('新微信号 tenant_id 为空', () => assert.equal(storage.getAuth().tenant_id, null))
  check('新微信号孩子列表为空', () => assert.equal(newParent.students.length, 0))

  // 无效码
  const invalidError = await auth.bindChild('ZZZZ-ZZZZ').catch((error) => error)
  check('无效码报 INVITE_CODE_INVALID', () =>
    assert.equal(invalidError && invalidError.code, 'INVITE_CODE_INVALID')
  )
  const invalidText = session.describeBindError(invalidError)
  check('无效码文案提到邀请码', () => assert.ok(invalidText.message.includes('邀请码')))
  check('无效码动作是可重试', () => assert.equal(invalidText.action, 'retry'))

  // 绑第一个
  await auth.bindChild(codeForFirst)
  const afterBind = storage.getAuth()
  check('绑定后 active_student_id 指向该孩子', () =>
    assert.equal(afterBind.active_student_id, studentA.id)
  )
  check('绑定后 tenant_id 有值', () => assert.ok(afterBind.tenant_id))
  check('绑定后 role 仍是 parent', () => assert.equal(afterBind.role, 'parent'))
  check('绑定后 bound_students 回填为 1', () => assert.equal(afterBind.bound_students, 1))

  const listedAfterBind = await http.get('/me/students', { silent: true })
  check('首页列表出现该孩子', () =>
    assert.ok(listedAfterBind.items.some((item) => item.student_id === studentA.id))
  )

  // 同一学生对同一家长重复绑：新码 + 已绑定 → BINDING_EXISTS
  const secondCodeForFirst = await issueCode(studentA.id, 24)
  const duplicateError = await auth.bindChild(secondCodeForFirst).catch((error) => error)
  check('重复绑定报 BINDING_EXISTS', () =>
    assert.equal(duplicateError && duplicateError.code, 'BINDING_EXISTS')
  )
  check('重复绑定的文案引导去切换孩子', () =>
    assert.equal(session.describeBindError(duplicateError).action, 'switch')
  )

  // ---------- 10. 多孩子：绑第二个 → 切换 ----------
  const codeForSecond = await issueCode(studentB.id, 24)
  await auth.bindChild(codeForSecond)
  const twoKids = storage.getAuth()
  check('绑两个孩子后 bound_students = 2', () => assert.equal(twoKids.bound_students, 2))
  check('绑第二个后 active 指向第二个', () =>
    assert.equal(twoKids.active_student_id, studentB.id)
  )

  const listTwo = await http.get('/me/students', { silent: true })
  check('/me/students 返回 2 个孩子', () => assert.equal(listTwo.items.length, 2))
  check('第二个孩子被标记 active', () =>
    assert.equal(listTwo.items.find((item) => item.student_id === studentB.id).active, true)
  )

  // 切回第一个
  const tenantOfFirst = listTwo.items.find((item) => item.student_id === studentA.id).tenant_id
  await auth.switchStudent(studentA.id)
  const afterSwitch = storage.getAuth()
  check('切换后 active_student_id 变回第一个', () =>
    assert.equal(afterSwitch.active_student_id, studentA.id)
  )
  check('切换后 tenant_id 跟着变', () => assert.equal(afterSwitch.tenant_id, tenantOfFirst))
  check('切换后 role 仍是 parent', () => assert.equal(afterSwitch.role, 'parent'))
  check('切换后 bound_students 不变', () => assert.equal(afterSwitch.bound_students, 2))

  const listAfterSwitch = await http.get('/me/students', { silent: true })
  check('列表里 active 标记转移到第一个', () =>
    assert.equal(
      listAfterSwitch.items.find((item) => item.student_id === studentA.id).active,
      true
    )
  )
  check('另一个孩子 active = false', () =>
    assert.equal(
      listAfterSwitch.items.find((item) => item.student_id === studentB.id).active,
      false
    )
  )

  // 跨机构：库里只有一个机构时，机构名不会变，这是正常的
  const tenantNames = new Set(listAfterSwitch.items.map((item) => item.tenant_name))
  if (tenantNames.size >= 2) {
    const nameOfFirst = listAfterSwitch.items.find((item) => item.student_id === studentA.id)
      .tenant_name
    const nameOfSecond = listAfterSwitch.items.find((item) => item.student_id === studentB.id)
      .tenant_name
    check('跨机构两个孩子的机构名不同', () => assert.notEqual(nameOfFirst, nameOfSecond))
  } else {
    console.log('  skip 跨机构切换断言（库里这两个孩子同机构）')
  }

  // ---------- 11. 切换到一个不在绑定集合里的孩子 ----------
  const outsiderError = await auth
    .switchStudent('00000000-0000-4000-8000-000000000000')
    .catch((error) => error)
  check('切到未绑定孩子报 OUT_OF_BINDING_SET', () =>
    assert.equal(outsiderError && outsiderError.code, 'OUT_OF_BINDING_SET')
  )
  check('该错误的文案可读', () => assert.ok(session.describeSwitchError(outsiderError).length > 0))
  check('切换失败后 active 没有被改坏', () =>
    assert.equal(storage.getAuth().active_student_id, studentA.id)
  )

  report()
}

function report() {
  console.log(`\n${passed} passed, ${failed} failed`)
  process.exit(failed === 0 ? 0 : 1)
}

main().catch((error) => {
  console.error('live smoke 崩了：', error)
  process.exit(1)
})
