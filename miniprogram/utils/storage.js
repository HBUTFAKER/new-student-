/**
 * 登录态存储。
 *
 * 只存 token 与「当前身份上下文」（role / tenant_id / active_student_id / bound_students）。
 * 红线：openid / unionid 一律不落地（06 §2.1：身份识别全在服务端）。
 */

'use strict'

var AUTH_KEY = 'auth'

function readAuth() {
  try {
    var value = wx.getStorageSync(AUTH_KEY)
    return value && typeof value === 'object' ? value : null
  } catch (error) {
    return null
  }
}

function getAuth() {
  return readAuth()
}

function setAuth(payload) {
  try {
    wx.setStorageSync(AUTH_KEY, payload || {})
  } catch (error) {
    // storage 写失败不影响本次会话（内存里还有 token），只是冷启动会重新登录
  }
}

/** 局部更新（如登录后回填 bound_students），不覆盖已有字段 */
function patchAuth(patch) {
  var current = readAuth() || {}
  setAuth(Object.assign({}, current, patch || {}))
}

function clearAuth() {
  try {
    wx.removeStorageSync(AUTH_KEY)
  } catch (error) {
    // ignore
  }
}

function getAccessToken() {
  var auth = readAuth()
  return auth && auth.access_token ? auth.access_token : ''
}

function getRefreshToken() {
  var auth = readAuth()
  return auth && auth.refresh_token ? auth.refresh_token : ''
}

function saveTokens(pair) {
  var auth = readAuth() || {}
  auth.access_token = pair.access_token
  if (pair.refresh_token) auth.refresh_token = pair.refresh_token
  setAuth(auth)
}

module.exports = {
  AUTH_KEY: AUTH_KEY,
  getAuth: getAuth,
  setAuth: setAuth,
  patchAuth: patchAuth,
  clearAuth: clearAuth,
  getAccessToken: getAccessToken,
  getRefreshToken: getRefreshToken,
  saveTokens: saveTokens,
}
