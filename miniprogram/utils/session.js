/**
 * 会话合并与绑定错误映射（纯函数，不碰 wx.*，可在 Node 里直测）。
 *
 * 为什么单独一层：`POST /me/bindings` 和 `POST /me/active-student` 的响应里
 * 有 access/refresh token + tenant_id + active_student_id，**但没有 role 和
 * bound_students**。直接 Object.assign 到 storage 会把 role 抹成 undefined，
 * 把 bound_students 抹成旧值或丢字段。规则集中在这里，页面只调。
 */

'use strict'

/**
 * 合并一次「重签 token」的响应。
 *
 * @param {object|null} prev 现有 auth
 * @param {object} pair 绑定 / 切换接口的响应
 * @param {{boundStudents?: number}} [options] 调用方回填的实时绑定数量
 */
function mergeSession(prev, pair, options) {
  var base = prev && typeof prev === 'object' ? prev : {}
  var next = Object.assign({}, base)
  var patch = pair && typeof pair === 'object' ? pair : {}

  if (patch.access_token) next.access_token = patch.access_token
  if (patch.refresh_token) next.refresh_token = patch.refresh_token

  // 这两个字段允许显式 null（跨机构未激活），所以用「有没有这个键」判断，而不是真值判断
  if (Object.prototype.hasOwnProperty.call(patch, 'tenant_id')) {
    next.tenant_id = patch.tenant_id
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'active_student_id')) {
    next.active_student_id = patch.active_student_id
  }

  if (!next.role) next.role = 'parent'

  if (options && typeof options.boundStudents === 'number') {
    next.bound_students = options.boundStudents
  }

  return next
}

/** 邀请码归一化：去分隔符、转大写、8 位就补回 AB12-CD34 的形状 */
function normalizeInviteCode(raw) {
  var cleaned = String(raw == null ? '' : raw)
    .replace(/[^0-9A-Za-z]/g, '')
    .toUpperCase()
  if (cleaned.length === 8) return cleaned.slice(0, 4) + '-' + cleaned.slice(4)
  return cleaned
}

/**
 * 从扫码结果里取码。
 * 扫出来的可能是纯码，也可能是带参数的链接或一段说明文字，所以先按直读，
 * 再找带连字符的形状，最后找孤立的 8 位串。
 */
function extractInviteCode(raw) {
  var text = String(raw == null ? '' : raw).trim()
  var direct = normalizeInviteCode(text)
  if (direct.length === 9) return direct

  var upper = text.toUpperCase()
  var dashed = upper.match(/[0-9A-Z]{4}-[0-9A-Z]{4}/)
  if (dashed) return dashed[0]

  var plain = upper.match(/(?:^|[^0-9A-Z])([0-9A-Z]{8})(?![0-9A-Z])/)
  if (plain) return plain[1].slice(0, 4) + '-' + plain[1].slice(4)

  return direct
}

/**
 * 绑定失败的错误码映射。
 * 后端把「码不存在 / 已用过 / 已过期」统一归成 404 INVITE_CODE_INVALID，
 * 前端不编造更细的区分，只给一句能指导动作的话。
 *
 * action: retry（重输/重试）| switch（去切换孩子）| login（回登录页）| null（只提示）
 */
var BIND_ERRORS = {
  INVITE_CODE_INVALID: {
    message: '邀请码无效、已被使用或已过期，请向机构重新索取',
    action: 'retry',
  },
  BINDING_EXISTS: {
    message: '这个孩子已经在你名下绑定了，不用重复绑',
    action: 'switch',
  },
  STUDENT_ARCHIVED: {
    message: '该学生已归档，暂时不能绑定，请联系机构',
    action: null,
  },
  STUDENT_NOT_FOUND: {
    message: '找不到这个学生，请让机构确认邀请码是否发对了人',
    action: null,
  },
  OUT_OF_SCOPE: {
    message: '当前身份不能自助绑定，请用家长微信登录',
    action: null,
  },
  NETWORK_ERROR: {
    message: '连不上服务器，请检查网络后重试',
    action: 'retry',
  },
  SESSION_EXPIRED: {
    message: '登录状态已失效，请重新登录',
    action: 'login',
  },
}

function describeBindError(error) {
  var code = error && error.code ? error.code : ''
  var mapped = BIND_ERRORS[code]
  if (mapped) {
    return { code: code, message: mapped.message, action: mapped.action }
  }
  return {
    code: code,
    message: (error && error.message) || '绑定失败，请稍后重试',
    action: 'retry',
  }
}

/**
 * 切换孩子的错误映射。
 * 后端的 403 复用同一个 OUT_OF_SCOPE 表达「不是家长」和「孩子已归档」两件事，
 * 前端只能给一句通用话，别再往下猜。
 */
function describeSwitchError(error) {
  var code = error && error.code ? error.code : ''
  if (code === 'OUT_OF_BINDING_SET') {
    return '这个孩子已经不在你的绑定列表里了，下拉刷新看看'
  }
  if (code === 'OUT_OF_SCOPE') {
    return '暂时不能切换到该孩子（档案可能已归档），刷新看看'
  }
  if (code === 'NETWORK_ERROR') {
    return '连不上服务器，请检查网络后重试'
  }
  if (code === 'SESSION_EXPIRED') {
    return '登录状态已失效，请重新登录'
  }
  return (error && error.message) || '切换失败，请稍后重试'
}

module.exports = {
  mergeSession: mergeSession,
  normalizeInviteCode: normalizeInviteCode,
  extractInviteCode: extractInviteCode,
  describeBindError: describeBindError,
  describeSwitchError: describeSwitchError,
  BIND_ERRORS: BIND_ERRORS,
}
