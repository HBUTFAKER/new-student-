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
    // 真机 wx.request 会把 GET 的 data 拼成 query string，mock 必须照做，
    // 否则 /me/schedule?from=&to= 这类接口在冒烟里会丢参
    let target = url
    if (init.method === 'GET' && data && typeof data === 'object') {
      const query = Object.keys(data)
        .filter((key) => data[key] !== undefined && data[key] !== null)
        .map((key) => encodeURIComponent(key) + '=' + encodeURIComponent(data[key]))
        .join('&')
      if (query) target += (url.indexOf('?') >= 0 ? '&' : '?') + query
    }
    if (init.method !== 'GET' && data !== undefined) init.body = JSON.stringify(data)

    fetch(target, init)
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
const portal = require('../utils/portal')

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

async function checkAsync(name, fn) {
  try {
    await fn()
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
    if (method === 'GET') return http.get(url, options)
    if (method === 'PATCH') return http.patch(url, data, options)
    return http.post(url, data, options)
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

  // ---------- 12. 5.3 三页数据：课表 / 余额 / 明细 ----------
  // 造数走教务接口，断言用「增量」——脏库上跑第 N 次也不会假失败。
  const week = portal.weekRange(1) // 下一周：每天都还没开始，调课不会被「已开始」挡住

  // 同一库反复跑时，学生身上会累积上一轮的课次（跨班维度不允许同一孩子同一时段两节课）。
  // 先读一遍本周课表，再在空档里挑两个 110 分钟的空窗口，脚本才可重复执行。
  const busyBefore = await http.get('/me/schedule', {
    data: { from: week.from, to: week.to },
    silent: true,
  })
  const busyRanges = busyBefore.items.map((item) => [
    Date.parse(item.start_at),
    Date.parse(item.end_at),
  ])
  const picked = []
  for (let day = 0; day < 7 && picked.length < 2; day += 1) {
    const dayStart = Date.parse(week.start + 'T00:00:00+08:00') + day * 86400000
    for (let hour = 9; hour <= 18 && picked.length < 2; hour += 1) {
      const start = dayStart + hour * 3600000
      const end = start + 3600000
      const guardEnd = end + 50 * 60000 // 调课后要挪 10 分钟，留出余量
      const clash = busyRanges.some(([s, e]) => start < e && guardEnd > s)
      if (!clash) {
        picked.push({ start: start, end: end })
        busyRanges.push([start, guardEnd])
      }
    }
  }
  if (picked.length < 2) {
    console.log('  没有空档造课表（这一周排满了），跳过三页数据段')
    report()
    return
  }

  const course = await asAdmin('POST', '/courses', {
    name: `冒烟课程${stamp}`,
    subject: '物理',
    duration_min: 60,
  })
  const teacher = await asAdmin('POST', '/teachers', { name: `冒烟老师${stamp}` })
  const classroom = await asAdmin('POST', '/classrooms', {
    name: `冒烟教室${stamp}`,
    capacity: 10,
  })
  const klass = await asAdmin('POST', '/classes', {
    course_id: course.id,
    teacher_id: teacher.id,
    classroom_id: classroom.id,
    name: `冒烟班${stamp}`,
    term_start: week.start,
    term_end: week.end,
    capacity: 10,
  })
  await asAdmin('POST', `/classes/${klass.id}/students`, { student_id: studentA.id })

  const lessonOne = await asAdmin('POST', '/lessons', {
    class_id: klass.id,
    classroom_id: classroom.id,
    teacher_id: teacher.id,
    start_at: portal.isoDateTime(picked[0].start),
    end_at: portal.isoDateTime(picked[0].end),
  })
  const lessonTwo = await asAdmin('POST', '/lessons', {
    class_id: klass.id,
    classroom_id: classroom.id,
    teacher_id: teacher.id,
    start_at: portal.isoDateTime(picked[1].start),
    end_at: portal.isoDateTime(picked[1].end),
  })
  // 调课 +10 分钟 → status=moved（家长端要标「已调」）；空窗口留了 50 分钟余量，不会撞
  await asAdmin('PATCH', `/lessons/${lessonTwo.id}`, {
    start_at: portal.isoDateTime(picked[1].start + 10 * 60000),
    end_at: portal.isoDateTime(picked[1].end + 10 * 60000),
  })

  const schedule = await http.get('/me/schedule', {
    data: { from: week.from, to: week.to },
    silent: true,
  })
  const lessonIds = schedule.items.map((item) => item.lesson_id)
  check('课表返回我们造的两节课', () => {
    assert.ok(lessonIds.includes(lessonOne.id))
    assert.ok(lessonIds.includes(lessonTwo.id))
  })
  check('调过的那节 status = moved', () => {
    const moved = schedule.items.find((item) => item.lesson_id === lessonTwo.id)
    assert.equal(moved.status, 'moved')
  })
  check('课表带班级名 / 老师 / 科目', () => {
    const normal = schedule.items.find((item) => item.lesson_id === lessonOne.id)
    assert.equal(normal.class_name, `冒烟班${stamp}`)
    assert.equal(normal.teacher_name, `冒烟老师${stamp}`)
    assert.equal(normal.subject, '物理')
  })
  check('课表时间是 +08:00', () => assert.ok(schedule.items[0].start_at.endsWith('+08:00')))
  check('课表按时间升序', () => {
    const stamps = schedule.items.map((item) => item.start_at)
    assert.deepEqual(stamps, stamps.slice().sort())
  })
  check('课表窗口回显 from/to', () => {
    assert.equal(schedule.from, week.from)
    assert.equal(schedule.to, week.to)
  })

  // 余额：先记基线，再买 20 节、调减 3 节 → 增量必须正好 +17
  const balanceBefore = await http.get('/me/balance', { silent: true })
  const ledgerBefore = await http.get('/me/ledger', { silent: true })
  const purchase = await asAdmin('POST', '/purchases', {
    student_id: studentA.id,
    course_id: course.id,
    lesson_count: 20,
    gift_lesson_count: 0,
    valid_months: 12,
    amount: '2400.00',
  })
  await asAdmin('POST', '/ledger/adjustments', {
    student_id: studentA.id,
    purchase_id: purchase.id,
    delta: -1,
    reason: '冒烟调减 1',
  })
  await asAdmin('POST', '/ledger/adjustments', {
    student_id: studentA.id,
    purchase_id: purchase.id,
    delta: -2,
    reason: '冒烟调减 2',
  })
  const balanceAfter = await http.get('/me/balance', { silent: true })
  check('余额增量 = 20 - 3 = 17', () =>
    assert.equal(balanceAfter.total - balanceBefore.total, 17)
  )
  check('余额里能看到这个新包（剩余 17）', () => {
    const pack = balanceAfter.packages.find((item) => item.purchase_id === purchase.id)
    assert.ok(pack)
    assert.equal(pack.remaining, 17)
  })
  check('包有到期日', () => {
    const pack = balanceAfter.packages.find((item) => item.purchase_id === purchase.id)
    assert.ok(pack.expires_at)
  })
  await checkAsync('余额与后台 /students/{id}/balance 一致', async () => {
    const adminView = await asAdmin('GET', `/students/${studentA.id}/balance`)
    assert.equal(adminView.total, balanceAfter.total)
  })

  const ledgerAfter = await http.get('/me/ledger', { silent: true })
  check('流水条数 = 基线 + 3（缴费 1 + 调减 2）', () =>
    assert.equal(ledgerAfter.total, ledgerBefore.total + 3)
  )
  check('最新一条是调减 -2', () => {
    assert.equal(ledgerAfter.items[0].delta, -2)
    assert.equal(ledgerAfter.items[0].type, 'adjust')
  })
  check('流水行带类型文案字段可渲染', () => {
    const decorated = portal.decorateLedger(ledgerAfter.items[0])
    assert.ok(decorated.typeLabel.length > 0)
    assert.equal(decorated.minus, true)
    assert.match(decorated.timeText, /^\d{2}-\d{2} \d{2}:\d{2}$/)
  })
  await checkAsync('type 筛选只留 adjust', async () => {
    const onlyAdjust = await http.get('/me/ledger', {
      data: { type: 'adjust', page_size: 100 },
      silent: true,
    })
    assert.ok(onlyAdjust.items.every((item) => item.type === 'adjust'))
  })
  await checkAsync('分页 page_size=1 只回 1 条', async () => {
    const one = await http.get('/me/ledger', { data: { page_size: 1 }, silent: true })
    assert.equal(one.items.length, 1)
    assert.equal(one.total, ledgerAfter.total)
  })

  // 切孩子后三页数据必须跟着变（学生 B 没造课也没缴费）
  await auth.switchStudent(studentB.id)
  const otherSchedule = await http.get('/me/schedule', {
    data: { from: week.from, to: week.to },
    silent: true,
  })
  const otherBalance = await http.get('/me/balance', { silent: true })
  check('切到另一个孩子后课表里没有这两节课', () => {
    const ids = otherSchedule.items.map((item) => item.lesson_id)
    assert.equal(ids.includes(lessonOne.id), false)
    assert.equal(ids.includes(lessonTwo.id), false)
  })
  check('切孩子后余额跟着变', () => assert.notEqual(otherBalance.total, balanceAfter.total))
  await auth.switchStudent(studentA.id)
  const backBalance = await http.get('/me/balance', { silent: true })
  check('切回来余额复原', () => assert.equal(backBalance.total, balanceAfter.total))

  // ---------- 13. 5.4 订阅授权收集 ----------
  const templates = await http.get('/me/subscribe-templates', { silent: true })
  check('模板列表来自后端配置（3 条）', () => assert.equal(templates.items.length, 3))
  check('模板带 type / scene，小程序不硬编码', () => {
    templates.items.forEach((item) => {
      assert.ok(item.template_id)
      assert.ok(item.type === 'one_time' || item.type === 'long_term')
      assert.ok(item.scene)
    })
  })

  // 弹窗 mock：全部 accept，并记下每次请求的模板 id
  const asked = []
  global.wx.requestSubscribeMessage = function (options) {
    asked.push(options.tmplIds.slice())
    const response = { errMsg: 'requestSubscribeMessage:ok' }
    options.tmplIds.forEach((id) => {
      response[id] = 'accept'
    })
    options.success(response)
  }

  // runner 依赖 request/config，必须在 BASE 覆盖之后才 require（模块单例）
  const subscribeRunner = require('../utils/subscribe-runner')
  const collected = await subscribeRunner.collect({ force: true })
  check('一次弹窗最多带 3 个模板（微信限制）', () => {
    assert.equal(asked.length, 1)
    assert.equal(asked[0].length, 3)
  })
  check('全 accept 时 3 条都拿到结果', () => assert.equal(collected.length, 3))

  const secondRun = await subscribeRunner.collect()
  check('问过的模板不再自动弹', () => assert.equal(asked.length, 1))
  check('第二次 collect 为空（本地标记挡住）', () => assert.equal(secondRun.length, 0))

  // 同一库反复跑时额度会累积，所以先「reject 读基线」——reject 不改额度，
  // 返回的 available_count 就是当前值，比再开一个查询接口省事。
  const remind = templates.items[0].template_id
  const baseline = await http.post(
    '/me/subscribe-auths',
    { results: [{ template_id: remind, status: 'reject' }] },
    { silent: true },
  )
  const baselineCount = baseline.items[0].available_count
  check('reject 单独发也能落库', () => {
    assert.equal(baseline.items[0].status, 'rejected')
    assert.ok(baselineCount >= 1) // 上一段 collect 已经 accept 过一次
  })

  const acceptAgain = await http.post(
    '/me/subscribe-auths',
    { results: [{ template_id: remind, status: 'accept' }] },
    { silent: true },
  )
  check('再 accept 一次额度 +1', () => {
    assert.equal(acceptAgain.items[0].available_count, baselineCount + 1)
    assert.equal(acceptAgain.items[0].status, 'authorized')
  })

  const rejectOne = await http.post(
    '/me/subscribe-auths',
    { results: [{ template_id: remind, status: 'reject' }] },
    { silent: true },
  )
  check('reject 只改状态，额度不清零', () => {
    assert.equal(rejectOne.items[0].status, 'rejected')
    assert.equal(rejectOne.items[0].available_count, baselineCount + 1)
  })

  const banOne = await http.post(
    '/me/subscribe-auths',
    { results: [{ template_id: templates.items[1].template_id, status: 'ban' }] },
    { silent: true },
  )
  check('ban 落 banned', () => assert.equal(banOne.items[0].status, 'banned'))

  let unknownError = null
  try {
    await http.post(
      '/me/subscribe-auths',
      { results: [{ template_id: 'not-configured', status: 'accept' }] },
      { silent: true },
    )
  } catch (error) {
    unknownError = error
  }
  check('未配置的模板整批 422', () => {
    assert.ok(unknownError)
    assert.equal(unknownError.status, 422)
    assert.equal(unknownError.code, 'SUBSCRIBE_TEMPLATE_UNKNOWN')
  })

  check('storage 里没有 openid / unionid（红线）', () => {
    const raw = JSON.stringify(Array.from(store.entries()))
    assert.equal(raw.includes('openid'), false)
    assert.equal(raw.includes('unionid'), false)
  })

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
