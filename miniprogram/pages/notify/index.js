'use strict'

/**
 * 通知设置（033 可选做）：给家长一个反悔的口子。
 *
 * 首页进来时自动弹过一轮，这页只做两件事：看当前授权状态、手动重新授权。
 * 重新授权 = 忽略本地标记再弹一轮（`force: true`），拿到 accept 后端额度继续累加。
 */

var runner = require('../../utils/subscribe-runner')

Page({
  data: {
    loading: true,
    error: '',
    supported: true,
    asking: false,
    rows: [],
  },

  onShow: function () {
    this.load()
  },

  onPullDownRefresh: function () {
    var that = this
    this.load().then(function () {
      wx.stopPullDownRefresh()
    })
  },

  load: function () {
    var that = this
    this.setData({ loading: true, error: '' })
    return runner
      .snapshot()
      .then(function (result) {
        that.setData({
          loading: false,
          supported: result.supported,
          rows: result.rows,
        })
      })
      .catch(function (error) {
        that.setData({
          loading: false,
          error: (error && error.message) || '加载失败，请稍后重试',
        })
      })
  },

  onAuthorize: function () {
    var that = this
    if (this.data.asking) return
    if (!this.data.supported) {
      wx.showToast({ title: '当前微信版本不支持订阅消息', icon: 'none' })
      return
    }

    this.setData({ asking: true })
    runner
      .collect({ force: true })
      .then(function (results) {
        that.setData({ asking: false })
        wx.showToast({
          title: results && results.length ? '授权已更新' : '没有新的授权',
          icon: 'none',
        })
        return that.load()
      })
      .catch(function () {
        that.setData({ asking: false })
      })
  },
})
