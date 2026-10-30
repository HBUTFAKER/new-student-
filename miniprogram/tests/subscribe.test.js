'use strict'

/**
 * 订阅授权收集的纯逻辑单测（033 / 5.4）。
 *
 * 覆盖：模板归一化、拆 3 个一批、拒绝/接受后不再弹、收集版本失效、
 * storage 标记读写（坏数据兜底）、wx 回调解析、标记合并、状态文案。
 */

var test = require('node:test')
var assert = require('node:assert')

var subscribe = require('../utils/subscribe')

function memoryStore(initial) {
  var box = initial === undefined ? {} : initial
  return {
    get: function (key) {
      return box[key] === undefined ? null : box[key]
    },
    set: function (key, value) {
      box[key] = value
    },
    raw: function () {
      return box
    },
  }
}

var TEMPLATES = [
  { template_id: 'tpl_remind', type: 'one_time', scene: 'lesson_remind' },
  { template_id: 'tpl_balance', type: 'one_time', scene: 'balance_warn' },
  { template_id: 'tpl_cancel', type: 'long_term', scene: 'lesson_cancelled' },
  { template_id: 'tpl_extra', type: 'one_time', scene: 'custom' },
]

test('normalizeTemplates 去掉空 id 与重复', function () {
  var rows = subscribe.normalizeTemplates({
    items: [
      { template_id: 'a', type: 'one_time', scene: 'x' },
      { template_id: 'a', type: 'long_term', scene: 'y' },
      { template_id: '  ', scene: 'z' },
      { scene: 'no-id' },
      null,
    ],
  })
  assert.deepEqual(
    rows.map(function (item) {
      return item.template_id
    }),
    ['a']
  )
  assert.equal(rows[0].type, 'one_time')
})

test('normalizeTemplates 对非 one_time 的类型统一按 one_time 处理', function () {
  var rows = subscribe.normalizeTemplates({ items: [{ template_id: 'a', type: 'weird' }] })
  assert.equal(rows[0].type, 'one_time')
})

test('没标记时 4 个模板拆成 3 + 1 两批', function () {
  var plan = subscribe.batches(TEMPLATES, subscribe.readState(memoryStore()))
  assert.deepEqual(plan, [['tpl_remind', 'tpl_balance', 'tpl_cancel'], ['tpl_extra']])
})

test('每批最多 3 个（微信限制）', function () {
  var many = []
  for (var index = 0; index < 7; index += 1) {
    many.push({ template_id: 'tpl_' + index, type: 'one_time', scene: '' })
  }
  var plan = subscribe.batches(many, { version: subscribe.COLLECT_VERSION, marks: {} })
  assert.equal(plan.length, 3)
  plan.forEach(function (batch) {
    assert.ok(batch.length <= 3)
  })
  assert.equal(plan.reduce(function (sum, batch) { return sum + batch.length }, 0), 7)
})

test('accept 过的模板不再自动弹', function () {
  var store = memoryStore()
  var state = subscribe.mergeResults(subscribe.readState(store), [
    { template_id: 'tpl_remind', status: 'accept' },
  ])
  subscribe.writeState(store, state)

  var plan = subscribe.batches(TEMPLATES, subscribe.readState(store))
  assert.deepEqual(plan, [['tpl_balance', 'tpl_cancel', 'tpl_extra']])
})

test('reject / ban 过的模板也不再自动弹', function () {
  var store = memoryStore()
  var state = subscribe.mergeResults(subscribe.readState(store), [
    { template_id: 'tpl_remind', status: 'reject' },
    { template_id: 'tpl_balance', status: 'ban' },
  ])
  subscribe.writeState(store, state)

  var plan = subscribe.batches(TEMPLATES, subscribe.readState(store))
  assert.deepEqual(plan, [['tpl_cancel', 'tpl_extra']])
})

test('全部问过之后没有待弹批次', function () {
  var state = subscribe.mergeResults(subscribe.readState(memoryStore()), [
    { template_id: 'tpl_remind', status: 'accept' },
    { template_id: 'tpl_balance', status: 'reject' },
    { template_id: 'tpl_cancel', status: 'ban' },
    { template_id: 'tpl_extra', status: 'accept' },
  ])
  assert.deepEqual(subscribe.batches(TEMPLATES, state), [])
})

test('force 忽略本地标记，手动重新授权能再次弹', function () {
  var state = subscribe.mergeResults(subscribe.readState(memoryStore()), [
    { template_id: 'tpl_remind', status: 'reject' },
  ])
  var plan = subscribe.batches(TEMPLATES, state, { force: true })
  assert.deepEqual(plan, [['tpl_remind', 'tpl_balance', 'tpl_cancel'], ['tpl_extra']])
})

test('收集版本号变了，旧标记整体失效', function () {
  var store = memoryStore({
    subscribe: { version: subscribe.COLLECT_VERSION - 1, marks: { tpl_remind: { status: 'reject' } } },
  })
  var state = subscribe.readState(store)
  assert.deepEqual(state.marks, {})
  assert.equal(subscribe.batches(TEMPLATES, state).length, 2)
})

test('storage 里是坏数据时当没问过（不抛异常）', function () {
  var broken = memoryStore({ subscribe: 'not-an-object' })
  assert.deepEqual(subscribe.readState(broken).marks, {})

  var throwing = {
    get: function () {
      throw new Error('storage boom')
    },
    set: function () {
      throw new Error('storage boom')
    },
  }
  assert.deepEqual(subscribe.readState(throwing).marks, {})
  assert.doesNotThrow(function () {
    subscribe.writeState(throwing, { version: 1, marks: {} })
  })
})

test('mergeResults 保留没问过的模板标记', function () {
  var first = subscribe.mergeResults(subscribe.readState(memoryStore()), [
    { template_id: 'tpl_remind', status: 'accept' },
  ])
  var second = subscribe.mergeResults(first, [{ template_id: 'tpl_balance', status: 'reject' }], 123)
  assert.equal(second.marks.tpl_remind.status, 'accept')
  assert.equal(second.marks.tpl_balance.status, 'reject')
  assert.equal(second.marks.tpl_balance.at, 123)
})

test('mergeResults 丢掉非法状态', function () {
  var state = subscribe.mergeResults(subscribe.readState(memoryStore()), [
    { template_id: 'tpl_remind', status: 'whatever' },
  ])
  assert.deepEqual(state.marks, {})
})

test('parseWxResult 只收 accept/reject/ban，缺结果的模板不记账', function () {
  var results = subscribe.parseWxResult(['a', 'b', 'c'], {
    errMsg: 'requestSubscribeMessage:ok',
    a: 'accept',
    b: 'reject',
    c: '',
  })
  assert.deepEqual(results, [
    { template_id: 'a', status: 'accept' },
    { template_id: 'b', status: 'reject' },
  ])
})

test('parseWxResult 对整次失败（只有 errMsg）返回空', function () {
  assert.deepEqual(
    subscribe.parseWxResult(['a'], { errMsg: 'requestSubscribeMessage:fail cancel' }),
    []
  )
  assert.deepEqual(subscribe.parseWxResult(['a'], null), [])
})

test('parseWxResult 兼容 ban', function () {
  assert.deepEqual(subscribe.parseWxResult(['a'], { a: 'ban' }), [
    { template_id: 'a', status: 'ban' },
  ])
})

test('summarize 给「通知设置」页出可渲染行与文案', function () {
  var state = subscribe.mergeResults(subscribe.readState(memoryStore()), [
    { template_id: 'tpl_remind', status: 'accept' },
    { template_id: 'tpl_balance', status: 'ban' },
  ])
  var rows = subscribe.summarize(TEMPLATES, state)

  assert.equal(rows.length, 4)
  assert.equal(rows[0].title, '上课提醒')
  assert.equal(rows[0].label, '已授权')
  assert.equal(rows[0].tone, 'on')
  assert.equal(rows[1].label, '不再询问')
  assert.equal(rows[1].tone, 'off')
  assert.equal(rows[2].label, '未授权')
  assert.equal(rows[2].tone, 'none')
  assert.equal(rows[3].title, 'custom')
})

test('describeStatus 兜底文案', function () {
  assert.equal(subscribe.describeStatus('accept'), '已授权')
  assert.equal(subscribe.describeStatus('reject'), '已拒绝')
  assert.equal(subscribe.describeStatus('ban'), '不再询问')
  assert.equal(subscribe.describeStatus(''), '未授权')
  assert.equal(subscribe.describeStatus(undefined), '未授权')
})

test('写入的标记结构带版本号，能被下一次读回', function () {
  var store = memoryStore()
  var state = subscribe.mergeResults(subscribe.readState(store), [
    { template_id: 'tpl_remind', status: 'accept' },
  ])
  subscribe.writeState(store, state)

  var raw = store.raw().subscribe
  assert.equal(raw.version, subscribe.COLLECT_VERSION)
  assert.equal(raw.marks.tpl_remind.status, 'accept')
  assert.equal(subscribe.readState(store).marks.tpl_remind.status, 'accept')
})
