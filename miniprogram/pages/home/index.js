'use strict'

var http = require('../../utils/request')
var storage = require('../../utils/storage')

function twoDigits(value) {
  return value < 10 ? '0' + value : '' + value
}

function nowText() {
  var now = new Date()
  return (
    twoDigits(now.getHours()) + ':' + twoDigits(now.getMinutes()) + ':' + twoDigits(now.getSeconds())
  )
}

Page({
  data: {
    loading: true,
    error: '',
    students: [],
    updatedAt: '',
    role: '',
  },

  onLoad: function () {
    var auth = storage.getAuth() || {}
    this.setData({ role: auth.role || '' })
  },

  onShow: function () {
    this.load()
  },

  onPullDownRefresh: function () {
    this.load().then(function () {
      wx.stopPullDownRefresh()
    })
  },

  /** 并发去重：onShow 与下拉刷新可能同时触发，只留一个在飞 */
  load: function (silent) {
    var that = this
    if (this.pending) return this.pending

    if (!silent) this.setData({ loading: true, error: '' })

    this.pending = http
      .get('/me/students', { silent: true })
      .then(function (result) {
        var students = ((result && result.items) || []).map(function (item) {
          // 头像首字在 JS 里取：WXML 里对字符串做下标取值兼容性不稳
          return Object.assign({}, item, { initial: (item.student_name || '?').slice(0, 1) })
        })
        that.setData({
          students: students,
          loading: false,
          error: '',
          updatedAt: nowText(),
        })
        return students
      })
      .catch(function (error) {
        that.setData({
          loading: false,
          error: that.describe(error),
        })
        return []
      })
      .then(function (value) {
        that.pending = null
        return value
      })

    return this.pending
  },

  describe: function (error) {
    if (!error) return '加载失败，请下拉重试'
    if (error.status === 403) return '当前账号没有查看孩子信息的权限，请联系机构。'
    return error.message || '加载失败，请下拉重试'
  },

  onRetry: function () {
    this.load()
  },

  goBind: function () {
    wx.navigateTo({ url: '/pages/bind-placeholder/index' })
  },

  onLogout: function () {
    var that = this
    wx.showModal({
      title: '退出登录',
      content: '退出后需要重新微信登录，确定吗？',
      success: function (result) {
        if (!result.confirm) return
        storage.clearAuth()
        wx.reLaunch({ url: '/pages/login/index' })
      },
    })
  },
})
