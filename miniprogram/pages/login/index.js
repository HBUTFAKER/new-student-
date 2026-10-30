'use strict'

var storage = require('../../utils/storage')
var auth = require('../../utils/auth')
var core = require('../../utils/request-core')

Page({
  data: {
    submitting: false,
    error: '',
  },

  onLoad: function () {
    // 冷启动已有 token：直接进首页，token 失效由 request 封装的 401 续期兜底
    if (storage.getAccessToken()) {
      wx.reLaunch({ url: '/pages/home/index' })
    }
  },

  onLogin: function () {
    var that = this
    if (this.data.submitting) return

    this.setData({ submitting: true, error: '' })

    auth
      .login()
      .then(function (result) {
        wx.reLaunch({
          url: result.needBind ? '/pages/bind-placeholder/index' : '/pages/home/index',
        })
      })
      .catch(function (error) {
        that.setData({
          submitting: false,
          error: that.describe(error),
        })
      })
  },

  /** 把各类失败翻成一句人话：登录页不能只弹 toast，得留在界面上看得见 */
  describe: function (error) {
    if (!error) return '登录失败，请重试'
    if (error.code === core.NETWORK_ERROR) {
      return '连不上服务器。请确认后端已启动，且开发者工具已勾选「不校验合法域名」。'
    }
    if (error.code === core.SESSION_EXPIRED) {
      return '登录状态已失效，请重新点击登录。'
    }
    return error.message || '登录失败，请重试'
  },

  onRetry: function () {
    this.onLogin()
  },
})
