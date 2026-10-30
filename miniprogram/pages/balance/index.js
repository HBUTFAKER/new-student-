'use strict'

/**
 * 课时余额（032 / 05 §9.4）。
 *
 * total 来自后端实时 SUM(delta)，前端不重算；负数照实显示并给欠课时提示。
 * 包列表只含「仍有效」的包（过期/耗尽的后端不下发），30 天内到期标警示色。
 */

var http = require('../../utils/request')
var portal = require('../../utils/portal')

Page({
  data: {
    loading: true,
    error: '',
    errorAction: '',
    balance: null,
    packages: [],
  },

  onLoad: function () {
    this.load()
  },

  onPullDownRefresh: function () {
    this.load(true).then(function () {
      wx.stopPullDownRefresh()
    })
  },

  load: function (silent) {
    var that = this
    if (this.pending) return this.pending
    if (silent !== true) this.setData({ loading: true, error: '', errorAction: '' })

    this.pending = http
      .get('/me/balance', { silent: true })
      .then(function (result) {
        var today = portal.businessToday()
        var packages = ((result && result.packages) || []).map(function (item) {
          return portal.decoratePackage(item, today)
        })
        that.setData({
          loading: false,
          error: '',
          errorAction: '',
          balance: portal.balanceTone((result && result.total) || 0),
          packages: packages,
        })
        return result
      })
      .catch(function (error) {
        var described = portal.describePortalError(error)
        that.setData({
          loading: false,
          error: described.message,
          errorAction: described.action || '',
          packages: [],
        })
        return null
      })
      .then(function (value) {
        that.pending = null
        return value
      })

    return this.pending
  },

  onRetry: function () {
    this.load()
  },

  goSwitch: function () {
    wx.navigateTo({ url: '/pages/switch-student/index' })
  },
})
