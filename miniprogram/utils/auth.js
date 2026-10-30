/**
 * 登录相关：wx.login 取 code → 换 token → 校验身份可用。
 * 5.1 只做登录，绑定邀请码是 5.2 的活。
 */

'use strict'

var config = require('./config')
var storage = require('./storage')
var http = require('./request')

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
 * 完整登录流程。
 * @returns {Promise<{needBind: boolean, students: Array}>}
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
        storage.patchAuth({ bound_students: students.length })
        return {
          // 以服务端实时绑定集合为准：登录响应里的 bound_students 是签发时的快照
          needBind: !token.tenant_id || students.length === 0,
          students: students,
        }
      })
    })
}

function logout() {
  storage.clearAuth()
  wx.reLaunch({ url: '/pages/login/index' })
}

module.exports = {
  getWxCode: getWxCode,
  login: login,
  logout: logout,
}
