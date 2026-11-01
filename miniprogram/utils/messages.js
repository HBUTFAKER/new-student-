/**
 * 站内信 / 红点（5.5）的纯逻辑层。
 *
 * 不碰 wx.*、不发请求，可在 Node 里直测：
 * - 红点数字的取整与「99+」截断
 * - 列表行的时间文案与未读态
 * - 单条已读 / 全部已读后本地列表的状态推进（避免整页重拉）
 */

'use strict'

var portal = require('./portal')

/** 红点文案：0 不显示，1–99 原样，≥100 收成 99+ */
function badgeText(unread) {
  var value = typeof unread === 'number' && unread > 0 ? Math.floor(unread) : 0
  if (value <= 0) return ''
  if (value > 99) return '99+'
  return '' + value
}

/** 一条消息 → 列表行需要的展示字段 */
function decorateMessage(item) {
  var row = item || {}
  return Object.assign({}, row, {
    timeText: portal.formatDateTime(row.created_at),
    unread: row.read !== true,
  })
}

function decorateList(items) {
  var list = items || []
  return list.map(decorateMessage)
}

/**
 * 单条已读：本地把对应行改成已读，并把 unread 减一（不重拉整页）。
 * 找不到 id 或本来就已读，返回原对象、unread 不变。
 */
function markOneRead(items, id, unread) {
  var list = items || []
  var remaining = typeof unread === 'number' ? unread : 0
  var hit = false
  var next = list.map(function (item) {
    if (item.id !== id || item.read === true) return item
    hit = true
    return Object.assign({}, item, { read: true, unread: false })
  })
  return {
    items: hit ? next : list,
    unread: hit ? Math.max(0, remaining - 1) : remaining,
    changed: hit,
  }
}

/** 全部已读：所有行置已读，unread 归零 */
function markAllRead(items) {
  var list = items || []
  return {
    items: list.map(function (item) {
      return item.read === true ? item : Object.assign({}, item, { read: true, unread: false })
    }),
    unread: 0,
  }
}

/** 首页红点的一行文案：0 → 空串（不渲染），其余交给 badgeText */
function homeBadge(unread) {
  return badgeText(unread)
}

module.exports = {
  badgeText: badgeText,
  decorateMessage: decorateMessage,
  decorateList: decorateList,
  markOneRead: markOneRead,
  markAllRead: markAllRead,
  homeBadge: homeBadge,
}
