'use strict'

/**
 * 周课表（032 / 05 §9.3）。
 *
 * 按自然周取数：窗口由 utils/portal.weekRange 算成带 +08:00 的 from/to，
 * 后端限 31 天，一周远小于上限。翻周时用 seq 丢掉晚到的旧响应。
 */

var http = require('../../utils/request')
var portal = require('../../utils/portal')

Page({
  data: {
    loading: true,
    error: '',
    errorAction: '',
    week: null,
    groups: [],
    total: 0,
  },

  onLoad: function () {
    this.offset = 0
    this.load(0)
  },

  onPullDownRefresh: function () {
    this.load(this.offset || 0, true).then(function () {
      wx.stopPullDownRefresh()
    })
  },

  load: function (offset, silent) {
    var that = this
    var week = portal.weekRange(offset)
    var seq = (this.seq || 0) + 1
    this.seq = seq
    this.offset = week.offset

    this.setData({ week: week, loading: silent !== true, error: '', errorAction: '' })

    return http
      .get('/me/schedule', { data: { from: week.from, to: week.to }, silent: true })
      .then(function (result) {
        if (seq !== that.seq) return null // 已经翻到别的周了，丢掉这次的响应
        var items = (result && result.items) || []
        that.setData({
          loading: false,
          error: '',
          errorAction: '',
          groups: portal.groupByDay(items),
          total: items.length,
        })
        return result
      })
      .catch(function (error) {
        if (seq !== that.seq) return null
        var described = portal.describePortalError(error)
        that.setData({
          loading: false,
          error: described.message,
          errorAction: described.action || '',
          groups: [],
          total: 0,
        })
        return null
      })
  },

  prevWeek: function () {
    this.load(this.offset - 1)
  },

  nextWeek: function () {
    this.load(this.offset + 1)
  },

  thisWeek: function () {
    if (this.offset === 0) return
    this.load(0)
  },

  onRetry: function () {
    this.load(this.offset || 0)
  },

  goSwitch: function () {
    wx.navigateTo({ url: '/pages/switch-student/index' })
  },
})
