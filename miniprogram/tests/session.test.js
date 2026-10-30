/**
 * 会话合并 / 邀请码归一化 / 错误映射单测（node:test，零依赖）。
 *
 *   node --test miniprogram/tests/session.test.js
 */

'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')

const session = require('../utils/session')

const PREV = {
  access_token: 'old-access',
  refresh_token: 'old-refresh',
  role: 'parent',
  tenant_id: 'tenant-a',
  active_student_id: 'student-a',
  bound_students: 1,
}

/** 绑定 / 切换接口的真实响应形状：有 token 和两个 id，没有 role / bound_students */
const PAIR = {
  access_token: 'new-access',
  refresh_token: 'new-refresh',
  token_type: 'bearer',
  expires_in: 7200,
  tenant_id: 'tenant-b',
  active_student_id: 'student-b',
}

test('mergeSession：token 与两个 id 用响应里的新值', () => {
  const next = session.mergeSession(PREV, PAIR)

  assert.equal(next.access_token, 'new-access')
  assert.equal(next.refresh_token, 'new-refresh')
  assert.equal(next.tenant_id, 'tenant-b')
  assert.equal(next.active_student_id, 'student-b')
})

test('mergeSession：role 保留原值（响应里没有这个字段）', () => {
  assert.equal(session.mergeSession(PREV, PAIR).role, 'parent')
})

test('mergeSession：bound_students 由调用方回填', () => {
  const next = session.mergeSession(PREV, PAIR, { boundStudents: 2 })
  assert.equal(next.bound_students, 2)
})

test('mergeSession：不回填时保留旧绑定数量', () => {
  assert.equal(session.mergeSession(PREV, PAIR).bound_students, 1)
})

test('mergeSession：不回填时 boundStudents 传 0 要生效', () => {
  assert.equal(session.mergeSession(PREV, PAIR, { boundStudents: 0 }).bound_students, 0)
})

test('mergeSession：tenant_id 显式 null 要写进去（跨机构未激活）', () => {
  const next = session.mergeSession(PREV, {
    access_token: 'x',
    refresh_token: 'y',
    tenant_id: null,
    active_student_id: null,
  })

  assert.equal(next.tenant_id, null)
  assert.equal(next.active_student_id, null)
})

test('mergeSession：响应缺 tenant_id 时不抹掉旧值', () => {
  const next = session.mergeSession(PREV, { access_token: 'x' })
  assert.equal(next.tenant_id, 'tenant-a')
  assert.equal(next.active_student_id, 'student-a')
})

test('mergeSession：旧会话为空时 role 兜底 parent', () => {
  const next = session.mergeSession(null, PAIR)
  assert.equal(next.role, 'parent')
  assert.equal(next.access_token, 'new-access')
})

test('mergeSession：不改动传入的 prev 对象', () => {
  const copy = Object.assign({}, PREV)
  session.mergeSession(copy, PAIR)
  assert.deepEqual(copy, PREV)
})

test('normalizeInviteCode：补连字符、转大写、去空格', () => {
  assert.equal(session.normalizeInviteCode('ab12cd34'), 'AB12-CD34')
  assert.equal(session.normalizeInviteCode('AB12-CD34'), 'AB12-CD34')
  assert.equal(session.normalizeInviteCode(' ab12 cd34 '), 'AB12-CD34')
})

test('normalizeInviteCode：长度不对就原样返回（交给后端报错）', () => {
  assert.equal(session.normalizeInviteCode('ABC'), 'ABC')
  assert.equal(session.normalizeInviteCode(''), '')
  assert.equal(session.normalizeInviteCode(null), '')
})

test('extractInviteCode：纯码 / 带连字符 / 大小写', () => {
  assert.equal(session.extractInviteCode('ab12cd34'), 'AB12-CD34')
  assert.equal(session.extractInviteCode('AB12-CD34'), 'AB12-CD34')
})

test('extractInviteCode：从链接里捞码', () => {
  assert.equal(
    session.extractInviteCode('https://example.com/bind?code=ab12cd34'),
    'AB12-CD34'
  )
  assert.equal(
    session.extractInviteCode('https://example.com/bind?code=AB12-CD34&from=qr'),
    'AB12-CD34'
  )
})

test('extractInviteCode：认不出就返回清洗后的原串（长度不为 9）', () => {
  assert.notEqual(session.extractInviteCode('hello world').length, 9)
  assert.equal(session.extractInviteCode('').length, 0)
})

test('describeBindError：码无效 → 可重试', () => {
  const result = session.describeBindError({ code: 'INVITE_CODE_INVALID', message: 'x' })
  assert.equal(result.action, 'retry')
  assert.ok(result.message.includes('邀请码'))
})

test('describeBindError：已绑定 → 引导去切换孩子', () => {
  const result = session.describeBindError({ code: 'BINDING_EXISTS', message: '该孩子已绑定' })
  assert.equal(result.action, 'switch')
  assert.ok(result.message.includes('已经'))
})

test('describeBindError：学生归档 / 找不到 → 只提示不重试', () => {
  assert.equal(session.describeBindError({ code: 'STUDENT_ARCHIVED' }).action, null)
  assert.equal(session.describeBindError({ code: 'STUDENT_NOT_FOUND' }).action, null)
})

test('describeBindError：断网 / 登录失效', () => {
  assert.equal(session.describeBindError({ code: 'NETWORK_ERROR' }).action, 'retry')
  assert.equal(session.describeBindError({ code: 'SESSION_EXPIRED' }).action, 'login')
})

test('describeBindError：未收录的码回落到后端 message', () => {
  const result = session.describeBindError({ code: 'SOMETHING_NEW', message: '后端说的一句话' })
  assert.equal(result.message, '后端说的一句话')
  assert.equal(result.action, 'retry')
})

test('describeBindError：连 error 都没有也能给话', () => {
  assert.ok(session.describeBindError(null).message.length > 0)
})

test('describeSwitchError：不在绑定集合 / 已归档', () => {
  assert.ok(session.describeSwitchError({ code: 'OUT_OF_BINDING_SET' }).includes('绑定列表'))
  assert.ok(session.describeSwitchError({ code: 'OUT_OF_SCOPE' }).includes('切换'))
})

test('describeSwitchError：未收录的码回落到后端 message', () => {
  assert.equal(session.describeSwitchError({ code: 'X', message: '别的话' }), '别的话')
})
