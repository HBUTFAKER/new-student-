/**
 * utils/messages.js 的单测（node:test，零框架）。
 * 跑法：node --test miniprogram/tests/messages.test.js
 */

'use strict'

var test = require('node:test')
var assert = require('node:assert')

var messages = require('../utils/messages')

test('badgeText：0/负数/非数字都不显示', function () {
  assert.equal(messages.badgeText(0), '')
  assert.equal(messages.badgeText(-3), '')
  assert.equal(messages.badgeText(undefined), '')
  assert.equal(messages.badgeText(null), '')
})

test('badgeText：1–99 原样，100 起收成 99+', function () {
  assert.equal(messages.badgeText(1), '1')
  assert.equal(messages.badgeText(99), '99')
  assert.equal(messages.badgeText(100), '99+')
  assert.equal(messages.badgeText(1234), '99+')
})

test('decorateMessage：read=false 显示未读，时间走 MM-DD HH:mm', function () {
  var row = messages.decorateMessage({
    id: 'm1',
    title: '小明 已签到扣课',
    body: '本节课已扣 1 课时，剩余 4 课时',
    read: false,
    created_at: '2026-11-01T19:05:00+08:00',
  })
  assert.equal(row.unread, true)
  assert.equal(row.timeText, '11-01 19:05')
  assert.equal(row.title, '小明 已签到扣课')
})

test('decorateMessage：read=true 就是已读（不靠字段缺失判断）', function () {
  assert.equal(messages.decorateMessage({ read: true }).unread, false)
  assert.equal(messages.decorateMessage({}).unread, true)
})

test('decorateList：空数组不炸', function () {
  assert.deepEqual(messages.decorateList([]), [])
  assert.deepEqual(messages.decorateList(undefined), [])
  assert.equal(messages.decorateList([{ id: 'a', read: false }]).length, 1)
})

test('markOneRead：命中未读行 → 置已读 + unread 减一', function () {
  var state = messages.markOneRead(
    [
      { id: 'a', read: false },
      { id: 'b', read: false },
    ],
    'a',
    2
  )
  assert.equal(state.changed, true)
  assert.equal(state.unread, 1)
  assert.equal(state.items[0].read, true)
  assert.equal(state.items[0].unread, false)
  assert.equal(state.items[1].read, false)
})

test('markOneRead：已经读过的不重复减', function () {
  var state = messages.markOneRead([{ id: 'a', read: true }], 'a', 1)
  assert.equal(state.changed, false)
  assert.equal(state.unread, 1)
})

test('markOneRead：id 不在列表里 → 原样返回', function () {
  var list = [{ id: 'a', read: false }]
  var state = messages.markOneRead(list, 'zzz', 1)
  assert.equal(state.changed, false)
  assert.equal(state.items, list)
  assert.equal(state.unread, 1)
})

test('markOneRead：unread 不会减到负数', function () {
  assert.equal(messages.markOneRead([{ id: 'a', read: false }], 'a', 0).unread, 0)
})

test('markAllRead：全部置已读 + unread 归零', function () {
  var state = messages.markAllRead([
    { id: 'a', read: false },
    { id: 'b', read: true },
  ])
  assert.equal(state.unread, 0)
  assert.equal(state.items[0].read, true)
  assert.equal(state.items[1].read, true)
})

test('homeBadge 与 badgeText 同口径', function () {
  assert.equal(messages.homeBadge(0), '')
  assert.equal(messages.homeBadge(7), '7')
  assert.equal(messages.homeBadge(120), '99+')
})
