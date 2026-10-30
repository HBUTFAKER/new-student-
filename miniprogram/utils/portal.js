/**
 * 家长端三页（课表 / 余额 / 明细）的纯逻辑层。
 *
 * 这里不碰 wx.*、不发请求，全部是可在 Node 里直测的函数：
 * - 周窗口的推算是「固定 +08:00」的，不跟随手机时区（家长在境外也不该看到错位的一天）
 * - 时间展示统一 MM-DD HH:mm（032 要求）
 * - 徽章 / 文案 / 分页判定都收在这里，页面只负责 setData
 */

'use strict'

var TZ_OFFSET_MS = 8 * 60 * 60 * 1000
var DAY_MS = 24 * 60 * 60 * 1000
var WEEK_DAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

function pad2(value) {
  return value < 10 ? '0' + value : '' + value
}

/** 把真实时间戳平移成「北京时间的字段」，之后一律用 getUTC* 读 */
function shifted(ms) {
  return new Date(ms + TZ_OFFSET_MS)
}

function isoDate(ms) {
  var d = shifted(ms)
  return d.getUTCFullYear() + '-' + pad2(d.getUTCMonth() + 1) + '-' + pad2(d.getUTCDate())
}

/** 带时区的 ISO 字符串，后端要求 from/to 必须带时区（否则 422） */
function isoDateTime(ms) {
  var d = shifted(ms)
  return (
    isoDate(ms) +
    'T' +
    pad2(d.getUTCHours()) +
    ':' +
    pad2(d.getUTCMinutes()) +
    ':' +
    pad2(d.getUTCSeconds()) +
    '+08:00'
  )
}

/** 本周一 00:00（北京时间）对应的真实时间戳 */
function startOfWeekMs(nowMs) {
  var d = shifted(nowMs)
  var weekday = (d.getUTCDay() + 6) % 7 // 周一=0 … 周日=6
  var midnight = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate())
  return midnight - weekday * DAY_MS - TZ_OFFSET_MS
}

function weekLabel(offset, startMs) {
  if (offset === 0) return '本周'
  if (offset === -1) return '上周'
  if (offset === 1) return '下周'
  return isoDate(startMs).slice(5) + ' ~ ' + isoDate(startMs + 6 * DAY_MS).slice(5)
}

/**
 * 第 offset 周的查询窗口。offset=0 是本周，-1 上周，1 下周。
 * 回到本周时 label 会变回「本周」，页头据此高亮。
 */
function weekRange(offset, nowMs) {
  var base = startOfWeekMs(typeof nowMs === 'number' ? nowMs : Date.now()) + offset * 7 * DAY_MS
  return {
    offset: offset,
    from: isoDateTime(base),
    to: isoDateTime(base + 7 * DAY_MS),
    start: isoDate(base),
    end: isoDate(base + 6 * DAY_MS),
    label: weekLabel(offset, base),
  }
}

/** '2026-11-03T19:00:00+08:00' → '11-03 19:00' */
function formatDateTime(iso) {
  if (!iso) return ''
  var ms = new Date(iso).getTime()
  if (isNaN(ms)) return ''
  var d = shifted(ms)
  return pad2(d.getUTCMonth() + 1) + '-' + pad2(d.getUTCDate()) + ' ' + clock(ms)
}

/** '2026-11-03T19:00:00+08:00' → '19:00' */
function clock(ms) {
  var d = shifted(ms)
  return pad2(d.getUTCHours()) + ':' + pad2(d.getUTCMinutes())
}

/** 北京时间下的「今天」，YYYY-MM-DD */
function businessToday(nowMs) {
  return isoDate(typeof nowMs === 'number' ? nowMs : Date.now())
}

function statusBadge(status) {
  if (status === 'moved') return '已调'
  if (status === 'makeup') return '补课'
  return ''
}

function decorateLesson(item) {
  var startMs = new Date(item.start_at).getTime()
  var endMs = new Date(item.end_at).getTime()
  return Object.assign({}, item, {
    badge: statusBadge(item.status),
    dayKey: isoDate(startMs),
    dayLabel: isoDate(startMs).slice(5),
    span: clock(startMs) + '-' + clock(endMs),
  })
}

/**
 * 按北京时间自然日分组。返回 [{key, label, weekday, items}]，天按升序。
 * 后端已按 start_at 升序返回，这里只做一次稳定分组。
 */
function groupByDay(items) {
  var groups = []
  var index = {}
  var list = items || []
  list.forEach(function (raw) {
    var item = decorateLesson(raw)
    var group = index[item.dayKey]
    if (!group) {
      var d = shifted(new Date(raw.start_at).getTime())
      group = {
        key: item.dayKey,
        label: item.dayLabel,
        weekday: WEEK_DAYS[d.getUTCDay()],
        items: [],
      }
      index[item.dayKey] = group
      groups.push(group)
    }
    group.items.push(item)
  })
  return groups
}

var LEDGER_LABELS = {
  purchase: '缴费',
  grant: '赠送',
  consume: '上课扣课',
  adjust: '课时调整',
  refund: '退费',
  expire: '过期',
}

function ledgerTypeLabel(type) {
  return LEDGER_LABELS[type] || type || ''
}

function signedDelta(delta) {
  var value = typeof delta === 'number' ? delta : 0
  return value > 0 ? '+' + value : '' + value
}

function decorateLedger(item) {
  return Object.assign({}, item, {
    typeLabel: ledgerTypeLabel(item.type),
    deltaText: signedDelta(item.delta),
    minus: (item.delta || 0) < 0,
    timeText: formatDateTime(item.created_at),
  })
}

/** 分页：还有没有下一页 */
function nextPageState(page, pageSize, total) {
  var loaded = page * pageSize
  return {
    hasMore: loaded < (total || 0),
    nextPage: page + 1,
    loaded: loaded,
  }
}

/** 包到期提醒：30 天内（含今天）标警示色；已过期的包后端根本不下发 */
function isExpiringSoon(expiresAt, todayIso, days) {
  if (!expiresAt) return false
  var horizon = days || 30
  var today = todayIso || businessToday()
  var diff = Math.round(
    (Date.parse(expiresAt + 'T00:00:00+08:00') - Date.parse(today + 'T00:00:00+08:00')) / DAY_MS
  )
  return diff >= 0 && diff <= horizon
}

function decoratePackage(item, todayIso) {
  return Object.assign({}, item, {
    expiring: isExpiringSoon(item.expires_at, todayIso),
    expiresText: item.expires_at ? item.expires_at : '长期有效',
  })
}

/** 余额展示：负数要给红色 + 欠课时提示，不裁剪成 0 */
function balanceTone(total) {
  var value = typeof total === 'number' ? total : 0
  return {
    value: value,
    text: '' + value,
    negative: value < 0,
    hint: value < 0 ? '已欠课时，请联系机构补缴' : '',
  }
}

/**
 * 三页共用的错误文案。403 只可能是「当前孩子不在绑定集合里」或「没选孩子」，
 * 两种都得把家长送回选择孩子页——不要显示后端那句干巴巴的 OUT_OF_SCOPE。
 */
function describePortalError(error) {
  if (!error) return { message: '加载失败，请下拉重试', action: null }
  if (error.status === 403) {
    return { message: '当前孩子已不在你的绑定列表里，请重新选择孩子。', action: 'switch' }
  }
  if (error.status === 0) {
    return { message: '网络不通，请检查网络后下拉重试', action: null }
  }
  return { message: error.message || '加载失败，请下拉重试', action: null }
}

module.exports = {
  weekRange: weekRange,
  startOfWeekMs: startOfWeekMs,
  formatDateTime: formatDateTime,
  businessToday: businessToday,
  isoDate: isoDate,
  isoDateTime: isoDateTime,
  statusBadge: statusBadge,
  groupByDay: groupByDay,
  ledgerTypeLabel: ledgerTypeLabel,
  signedDelta: signedDelta,
  decorateLedger: decorateLedger,
  nextPageState: nextPageState,
  isExpiringSoon: isExpiringSoon,
  decoratePackage: decoratePackage,
  balanceTone: balanceTone,
  describePortalError: describePortalError,
}
