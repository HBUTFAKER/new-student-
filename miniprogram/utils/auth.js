/**
 * 登录、绑定、切换孩子。
 * 三个动作都会「重签 token」，区别只在入口和落地页。
 */

'use strict'

var config = require('./config')
var storage = require('./storage')
var http = require('./request')
var session = require('./session')

/** 拿一次性 wx_code。游客模式拿不到 code 时，开发态用假 code 兜底（见 config.js） */
function getWxCode() {
  return new Promise(function (resolve, reject) {
    wx.login({
      success: function (result) {
        if (result && result.code) {
          resolve(result.code)
          return
        }
        if (config.ALLOW_FAKE_WX_CODE) {
          resolve(config.FAKE_WX_CODE)
          return
        }
        reject(new Error('微信登录失败：未获取到登录凭证，请重试'))
      },
      fail: function () {
        if (config.ALLOW_FAKE_WX_CODE) {
          resolve(config.FAKE_WX_CODE)
          return
        }
        reject(new Error('微信登录失败，请检查网络后重试'))
      },
    })
  })
}

/**
 * 用「重签 token 的响应」重建会话。
 * role 保留原值，bound_students 用 /me/students 的实时条数（不能用 1 硬猜）。
 */
function applySession(pair, students) {
  var prev = storage.getAuth() || {}
  storage.setAuth(
    session.mergeSession(prev, pair, {
      boundStudents: students ? students.length : 0,
    })
  )
  http.resetSession()
}

/**
 * 完整登录流程。
 * @returns {Promise<{needBind: boolean, needSwitch: boolean, students: Array}>}
 */
function login() {
  return getWxCode()
    .then(function (code) {
      // 登录请求本身不带旧 token，也不参与 401 续期（否则会自己套自己）
      return http.post(
        '/auth/wechat-login',
        { wx_code: code },
        { skipAuth: true, skipRefresh: true, silent: true }
      )
    })
    .then(function (token) {
      storage.setAuth({
        access_token: token.access_token,
        refresh_token: token.refresh_token,
        role: token.role,
        tenant_id: token.tenant_id,
        active_student_id: token.active_student_id,
        bound_students: token.bound_students,
      })
      http.resetSession()

      // 验证身份可用：能读到自己的绑定集合，才说明 token + 后端链路都通
      return http.get('/me/students', { silent: true }).then(function (result) {
        var students = (result && result.items) || []
        applySession(token, students)
        return { students: students, needBind: false, needSwitch: false }
      })
    })
    .then(function (state) {
      // 分流：没绑过 → 绑定页；绑了但没激活孩子（多孩子未选）→ 选择孩子页
      // 以实时绑定集合为准，不用登录响应里 bound_students 的快照
      var auth = storage.getAuth() || {}
      state.needBind = state.students.length === 0
      state.needSwitch = !state.needBind && !auth.tenant_id
      return state
    })
}

/**
 * 凭邀请码绑定孩子。成功会重签 JWT（新 tenant_id / active_student_id）。
 * @returns {Promise<{students: Array}>}
 */
function bindChild(inviteCode) {
  return http
    .post('/me/bindings', { invite_code: inviteCode }, { silent: true })
    .then(function (payload) {
      // 绑定成功后再拉一次绑定集合：bound_students 必须是实时值
      return http.get('/me/students', { silent: true }).then(function (result) {
        var students = (result && result.items) || []
        applySession(payload, students)
        return { students: students }
      })
    })
}

/** 切换当前孩子。同样重签 JWT，绑定数量不变，不额外拉列表 */
function switchStudent(studentId) {
  return http
    .post('/me/active-student', { student_id: studentId }, { silent: true })
    .then(function (pair) {
      var prev = storage.getAuth() || {}
      storage.setAuth(session.mergeSession(prev, pair))
      http.resetSession()
      return pair
    })
}

function logout() {
  storage.clearAuth()
  wx.reLaunch({ url: '/pages/login/index' })
}

module.exports = {
  getWxCode: getWxCode,
  login: login,
  bindChild: bindChild,
  switchStudent: switchStudent,
  logout: logout,
}
