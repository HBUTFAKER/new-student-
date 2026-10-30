'use strict'

/**
 * 订阅消息授权收集的纯逻辑（033 / 5.4）。
 *
 * 不碰 `wx.*`：storage 由调用方注入 `{ get(key), set(key, value) }`，所以能直接
 * `node --test`。真正弹窗与上报在 `utils/subscribe-runner.js`。
 *
 * 三条规则（清单验收原文「拒绝后不反复弹窗」）：
 *
 * 1. 同一个收集版本内，问过的模板不再自动弹——accept 的额度已经在后端，
 *    reject / ban 是家长的明确拒绝，反复弹只会招人烦；
 * 2. 收集版本号（`COLLECT_VERSION`）变了，标记整体失效，重新问一轮；
 * 3. 微信一次 `wx.requestSubscribeMessage` 最多 3 个模板，多了拆批。
 */

var STORAGE_KEY = 'subscribe'
var COLLECT_VERSION = 1
var BATCH_SIZE = 3
var STATUSES = ['accept', 'reject', 'ban']

var SCENE_LABELS = {
  lesson_remind: '上课提醒',
  balance_warn: '课时不足提醒',
  lesson_cancelled: '课程取消提醒',
}

function toTemplate(raw) {
  if (!raw || typeof raw !== 'object') return null
  var id = typeof raw.template_id === 'string' ? raw.template_id.trim() : ''
  if (!id) return null
  return {
    template_id: id,
    type: raw.type === 'long_term' ? 'long_term' : 'one_time',
    scene: typeof raw.scene === 'string' ? raw.scene : '',
  }
}

/** 后端下发的模板列表 → 去重、丢掉空 id（后端已校验，这里防脏数据） */
function normalizeTemplates(payload) {
  var list = payload && payload.items ? payload.items : []
  var seen = {}
  var out = []
  list.forEach(function (raw) {
    var template = toTemplate(raw)
    if (!template || seen[template.template_id]) return
    seen[template.template_id] = true
    out.push(template)
  })
  return out
}

function emptyState() {
  return { version: COLLECT_VERSION, marks: {} }
}

/** 读 storage 标记；版本不符或结构坏了就当没问过 */
function readState(store) {
  var raw = null
  try {
    raw = store && store.get ? store.get(STORAGE_KEY) : null
  } catch (error) {
    raw = null
  }
  if (!raw || typeof raw !== 'object' || raw.version !== COLLECT_VERSION) return emptyState()

  var marks = {}
  var source = raw.marks && typeof raw.marks === 'object' ? raw.marks : {}
  Object.keys(source).forEach(function (id) {
    var mark = source[id] || {}
    if (STATUSES.indexOf(mark.status) < 0) return
    marks[id] = { status: mark.status, at: typeof mark.at === 'number' ? mark.at : 0 }
  })
  return { version: COLLECT_VERSION, marks: marks }
}

function writeState(store, state) {
  try {
    if (store && store.set) store.set(STORAGE_KEY, state)
  } catch (error) {
    // 写不进去就下次再问，不影响家长正常用
  }
}

function markOf(state, templateId) {
  var marks = (state && state.marks) || {}
  return Object.prototype.hasOwnProperty.call(marks, templateId) ? marks[templateId] : null
}

/** 本版本内问过的模板都不再自动弹；`options.force` 是「通知设置」页的手动重来 */
function pendingTemplates(templates, state, options) {
  var force = !!(options && options.force)
  return (templates || []).filter(function (template) {
    if (force) return true
    return !markOf(state, template.template_id)
  })
}

function chunk(list, size) {
  var step = size > 0 ? size : BATCH_SIZE
  var out = []
  for (var index = 0; index < list.length; index += step) {
    out.push(list.slice(index, index + step))
  }
  return out
}

/** 待问模板 → 一批批 template_id（每批 ≤ 3） */
function batches(templates, state, options) {
  var pending = pendingTemplates(templates, state, options)
  return chunk(
    pending.map(function (template) {
      return template.template_id
    }),
    BATCH_SIZE
  )
}

/**
 * 解析 `wx.requestSubscribeMessage` 的回调。
 *
 * 微信只回有结果的模板：用户直接关掉弹窗时，`errMsg` 是 fail、模板一个都不给。
 * 没有结果的模板**不记账**，下次还会问——这正是我们要的。
 */
function parseWxResult(templateIds, response) {
  var results = []
  ;(templateIds || []).forEach(function (id) {
    var value = response ? response[id] : ''
    if (STATUSES.indexOf(value) >= 0) {
      results.push({ template_id: id, status: value })
    }
  })
  return results
}

/** 把本次结果并进标记（不覆盖没问过的模板） */
function mergeResults(state, results, at) {
  var base = state && state.marks ? state : emptyState()
  var marks = {}
  Object.keys(base.marks).forEach(function (id) {
    marks[id] = base.marks[id]
  })
  var stamp = typeof at === 'number' ? at : Date.now()
  ;(results || []).forEach(function (item) {
    if (!item || STATUSES.indexOf(item.status) < 0) return
    marks[item.template_id] = { status: item.status, at: stamp }
  })
  return { version: COLLECT_VERSION, marks: marks }
}

function describeStatus(status) {
  if (status === 'accept') return '已授权'
  if (status === 'reject') return '已拒绝'
  if (status === 'ban') return '不再询问'
  return '未授权'
}

function sceneLabel(scene) {
  return SCENE_LABELS[scene] || scene || '通知'
}

/** 「通知设置」页用：模板 + 当前标记 → 可直接渲染的行 */
function summarize(templates, state) {
  return (templates || []).map(function (template) {
    var mark = markOf(state, template.template_id)
    var status = mark ? mark.status : ''
    return {
      template_id: template.template_id,
      scene: template.scene,
      title: sceneLabel(template.scene),
      type: template.type,
      status: status,
      label: describeStatus(status),
      tone: status === 'accept' ? 'on' : status ? 'off' : 'none',
    }
  })
}

module.exports = {
  STORAGE_KEY: STORAGE_KEY,
  COLLECT_VERSION: COLLECT_VERSION,
  BATCH_SIZE: BATCH_SIZE,
  normalizeTemplates: normalizeTemplates,
  readState: readState,
  writeState: writeState,
  pendingTemplates: pendingTemplates,
  batches: batches,
  chunk: chunk,
  parseWxResult: parseWxResult,
  mergeResults: mergeResults,
  describeStatus: describeStatus,
  sceneLabel: sceneLabel,
  summarize: summarize,
}
