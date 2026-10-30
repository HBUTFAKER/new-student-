'use strict'

var auth = require('../../utils/auth')
var http = require('../../utils/request')
var session = require('../../utils/session')

Page({
  data: {
    loading: true,
    error: '',
    students: [],
    switchingId: '',
  },

  onShow: function () {
    this.load()
  },

  onPullDownRefresh: function () {
    this.load().then(function () {
      wx.stopPullDownRefresh()
    })
  },

  load: function () {
    var that = this
    if (this.pending) return this.pending

    this.setData({ loading: true, error: '' })

    this.pending = http
      .get('/me/students', { silent: true })
      .then(function (result) {
        var students = ((result && result.items) || []).map(function (item) {
          return Object.assign({}, item, { initial: (item.student_name || '?').slice(0, 1) })
        })
        that.setData({ students: students, loading: false })
        return students
      })
      .catch(function (error) {
        that.setData({ loading: false, error: session.describeSwitchError(error) })
        return []
      })
      .then(function (value) {
        that.pending = null
        return value
      })

    return this.pending
  },

  onPick: function (event) {
    var that = this
    var id = event.currentTarget.dataset.id
    var active = event.currentTarget.dataset.active

    if (active) {
      wx.reLaunch({ url: '/pages/home/index' })
      return
    }
    if (this.data.switchingId) return

    this.setData({ switchingId: id, error: '' })

    auth
      .switchStudent(id)
      .then(function () {
        wx.reLaunch({ url: '/pages/home/index' })
      })
      .catch(function (error) {
        that.setData({
          switchingId: '',
          error: session.describeSwitchError(error),
        })
      })
  },

  goBind: function () {
    wx.navigateTo({ url: '/pages/bind/index' })
  },
})
