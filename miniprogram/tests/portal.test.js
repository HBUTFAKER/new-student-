/**
 * utils/portal.js 的单测（node:test，零框架）。
 * 跑法：node --test miniprogram/tests/portal.test.js
 */

'use strict'

var test = require('node:test')
var assert = require('node:assert')

var portal = require('../utils/portal')

var WED = Date.parse('2026-11-18T10:00:00+08:00') // 周三

test('weekRange(0)：本周一到下周一，窗口带 +08:00', function () {
  var range = portal.weekRange(0, WED)
  assert.equal(range.start, '2026-11-16')
  assert.equal(range.end, '2026-11-22')
  assert.equal(range.from, '2026-11-16T00:00:00+08:00')
  assert.equal(range.to, '2026-11-23T00:00:00+08:00')
  assert.equal(range.label, '本周')
})

test('weekRange(-1) 整个窗口往前挪 7 天', function () {
  var range = portal.weekRange(-1, WED)
  assert.equal(range.from, '2026-11-09T00:00:00+08:00')
  assert.equal(range.to, '2026-11-16T00:00:00+08:00')
  assert.equal(range.label, '上周')
})

test('weekRange(1) 是下周', function () {
  var range = portal.weekRange(1, WED)
  assert.equal(range.from, '2026-11-23T00:00:00+08:00')
  assert.equal(range.label, '下周')
})

test('weekRange(3) 用日期区间当标题', function () {
  assert.equal(portal.weekRange(3, WED).label, '12-07 ~ 12-13')
})

test('周日仍算本周（周日不能跳到下一周）', function () {
  var sunday = Date.parse('2026-11-22T23:30:00+08:00')
  assert.equal(portal.weekRange(0, sunday).start, '2026-11-16')
})

test('跨月的周窗口不塌', function () {
  var tuesday = Date.parse('2026-12-01T09:00:00+08:00')
  var range = portal.weekRange(0, tuesday)
  assert.equal(range.start, '2026-11-30')
  assert.equal(range.end, '2026-12-06')
})

test('北京时间已过零点、UTC 还在前一天时，周起点不能错', function () {
  // 2026-11-16 00:30 +08:00 = 2026-11-15 16:30 UTC
  var monday = Date.parse('2026-11-16T00:30:00+08:00')
  assert.equal(portal.weekRange(0, monday).start, '2026-11-16')
})

test('formatDateTime 走北京时间', function () {
  assert.equal(portal.formatDateTime('2026-11-16T10:00:00+00:00'), '11-16 18:00')
  assert.equal(portal.formatDateTime('2026-11-16T19:00:00+08:00'), '11-16 19:00')
})

test('formatDateTime 空值/脏值返回空串', function () {
  assert.equal(portal.formatDateTime(''), '')
  assert.equal(portal.formatDateTime(null), '')
  assert.equal(portal.formatDateTime('not-a-date'), '')
})

test('statusBadge 只给 moved / makeup 徽章', function () {
  assert.equal(portal.statusBadge('moved'), '已调')
  assert.equal(portal.statusBadge('makeup'), '补课')
  assert.equal(portal.statusBadge('normal'), '')
})

test('groupByDay 按天分组并带星期与时间段', function () {
  var groups = portal.groupByDay([
    {
      lesson_id: 'a',
      class_name: '冲刺A班',
      subject: '物理',
      teacher_name: '李老师',
      start_at: '2026-11-16T18:00:00+08:00',
      end_at: '2026-11-16T19:00:00+08:00',
      status: 'normal',
    },
    {
      lesson_id: 'b',
      class_name: '冲刺A班',
      subject: '物理',
      teacher_name: '李老师',
      start_at: '2026-11-16T19:00:00+08:00',
      end_at: '2026-11-16T20:00:00+08:00',
      status: 'moved',
    },
    {
      lesson_id: 'c',
      class_name: '冲刺B班',
      subject: '物理',
      teacher_name: '王老师',
      start_at: '2026-11-17T19:00:00+08:00',
      end_at: '2026-11-17T20:00:00+08:00',
      status: 'makeup',
    },
  ])

  assert.equal(groups.length, 2)
  assert.equal(groups[0].label, '11-16')
  assert.equal(groups[0].weekday, '周一')
  assert.equal(groups[0].items.length, 2)
  assert.equal(groups[0].items[1].badge, '已调')
  assert.equal(groups[1].weekday, '周二')
  assert.equal(groups[1].items[0].badge, '补课')
  assert.equal(groups[1].items[0].span, '19:00-20:00')
})

test('groupByDay 空课表返回空数组', function () {
  assert.deepEqual(portal.groupByDay([]), [])
  assert.deepEqual(portal.groupByDay(null), [])
})

test('ledgerTypeLabel 覆盖六种类型', function () {
  assert.equal(portal.ledgerTypeLabel('purchase'), '缴费')
  assert.equal(portal.ledgerTypeLabel('grant'), '赠送')
  assert.equal(portal.ledgerTypeLabel('consume'), '上课扣课')
  assert.equal(portal.ledgerTypeLabel('adjust'), '课时调整')
  assert.equal(portal.ledgerTypeLabel('refund'), '退费')
  assert.equal(portal.ledgerTypeLabel('expire'), '过期')
  assert.equal(portal.ledgerTypeLabel('unknown'), 'unknown')
})

test('signedDelta 正数带加号，负数原样', function () {
  assert.equal(portal.signedDelta(3), '+3')
  assert.equal(portal.signedDelta(-1), '-1')
  assert.equal(portal.signedDelta(0), '0')
})

test('decorateLedger 补文案并标出扣减方向', function () {
  var row = portal.decorateLedger({
    type: 'consume',
    delta: -1,
    class_name: '冲刺A班',
    created_at: '2026-11-16T19:05:00+08:00',
  })
  assert.equal(row.typeLabel, '上课扣课')
  assert.equal(row.deltaText, '-1')
  assert.equal(row.minus, true)
  assert.equal(row.timeText, '11-16 19:05')
})

test('nextPageState 按 total 判定还有没有下一页', function () {
  assert.equal(portal.nextPageState(1, 20, 25).hasMore, true)
  assert.equal(portal.nextPageState(1, 20, 25).nextPage, 2)
  assert.equal(portal.nextPageState(2, 20, 25).hasMore, false)
  assert.equal(portal.nextPageState(1, 20, 20).hasMore, false)
  assert.equal(portal.nextPageState(1, 20, 0).hasMore, false)
})

test('isExpiringSoon：30 天内警示，已过期不警示', function () {
  assert.equal(portal.isExpiringSoon('2026-12-01', '2026-11-16'), true)
  assert.equal(portal.isExpiringSoon('2026-11-16', '2026-11-16'), true)
  assert.equal(portal.isExpiringSoon('2027-05-01', '2026-11-16'), false)
  assert.equal(portal.isExpiringSoon('2026-11-15', '2026-11-16'), false)
  assert.equal(portal.isExpiringSoon(null, '2026-11-16'), false)
})

test('decoratePackage 补到期文案', function () {
  assert.equal(portal.decoratePackage({ expires_at: null }, '2026-11-16').expiresText, '长期有效')
  var soon = portal.decoratePackage({ expires_at: '2026-12-01' }, '2026-11-16')
  assert.equal(soon.expiring, true)
  assert.equal(soon.expiresText, '2026-12-01')
})

test('balanceTone 负数给红字提示，正数不给', function () {
  var negative = portal.balanceTone(-3)
  assert.equal(negative.negative, true)
  assert.equal(negative.text, '-3')
  assert.ok(negative.hint.length > 0)

  var normal = portal.balanceTone(17)
  assert.equal(normal.negative, false)
  assert.equal(normal.hint, '')
})

test('describePortalError：403 引导去选择孩子', function () {
  var forbidden = portal.describePortalError({ status: 403, message: 'OUT_OF_SCOPE' })
  assert.equal(forbidden.action, 'switch')
  assert.ok(forbidden.message.indexOf('重新选择孩子') >= 0)

  var network = portal.describePortalError({ status: 0 })
  assert.equal(network.action, null)

  var plain = portal.describePortalError({ status: 500, message: '服务器开小差了' })
  assert.equal(plain.message, '服务器开小差了')
})
