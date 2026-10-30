/**
 * 小程序侧请求封装：把 wx.request 适配成 Promise，接到 request-core 的管线上。
 * 业务代码只用 get / post / patch / del，不直接碰 wx.request。
 */

'use strict'

var config = require('./config')
var storage = require('./storage')
var core = require('./request-core')

function wxSend(options) {
  return new Promise(function (resolve, reject) {
    wx.request({
      url: options.url,
      method: options.method || 'GET',
      data: options.data,
      header: options.headers,
      timeout: options.timeout || config.REQUEST_TIMEOUT,
      dataType: 'json',
      success: function (response) {
        resolve({ statusCode: response.statusCode, data: response.data })
      },
      fail: function (error) {
        reject(error)
      },
    })
  })
}

var pipeline = core.createRequestPipeline({
  baseUrl: config.BASE_URL,
  timeout: config.REQUEST_TIMEOUT,
  getAccessToken: storage.getAccessToken,
  getRefreshToken: storage.getRefreshToken,
  saveTokens: storage.saveTokens,
  clearTokens: storage.clearAuth,
  onSessionExpired: function () {
    // reLaunch 会关掉所有页面栈，避免用户按返回键回到已失效的旧页面
    wx.reLaunch({ url: '/pages/login/index' })
  },
  toast: function (message) {
    wx.showToast({ title: message, icon: 'none', duration: 2200 })
  },
  send: wxSend,
})

module.exports = {
  request: pipeline.request,
  get: pipeline.get,
  post: pipeline.post,
  patch: pipeline.patch,
  del: pipeline.delete,
  resetSession: pipeline.resetSession,
  ApiError: core.ApiError,
  NETWORK_ERROR: core.NETWORK_ERROR,
  SESSION_EXPIRED: core.SESSION_EXPIRED,
}
