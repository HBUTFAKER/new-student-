'use strict'

/**
 * 课堂反馈（048）。
 *
 * 时间倒序，上拉加载下一页，下拉刷新回第一页。当前孩子从 storage 的
 * active_student_id 拿：切孩子会重签 token 并更新 storage，reLaunch 后本页
 * 重新 onLoad，列表自然跟随。
 */

var http = require('../../utils/request')
var storage = require('../../utils/storage')
var portal = require('../../utils/portal')

var PAGE_SIZE = 20

function decorateRows(rows, page) {
  return rows.map(function (row, index) {
    return Object.assign(portal.decorateFeedback(row), { key: page + '-' + index })
  })
}

Page({
  data: {
    loading: true,
    loadingMore: false,
    error: '',
    errorAction: '',
    items: [],
    total: 0,
    hasMore: false,
  },

  onLoad: function () {
    var auth = storage.getAuth() || {}
    this.studentId = auth.active_student_id || ''
    this.page = 1
    if (!this.studentId) {
      this.setData({ loading: false, error: '请先选择孩子', errorAction: 'switch' })
      return
    }
    this.load()
  },

  onPullDownRefresh: function () {
    if (!this.studentId) {
      wx.stopPullDownRefresh()
      return
    }
    this.page = 1
    this.load(true).then(function () {
      wx.stopPullDownRefresh()
    })
  },

  onReachBottom: function () {
    if (!this.data.hasMore || this.data.loadingMore || this.data.loading) return
    this.page += 1
    this.loadMore()
  },

  load: function (silent) {
    var that = this
    var page = this.page || 1
    var seq = (this.seq || 0) + 1
    this.seq = seq

    if (silent !== true) this.setData({ loading: true, error: '', errorAction: '' })

    return http
      .get('/me/children/' + this.studentId + '/feedback', {
        data: { page: page, page_size: PAGE_SIZE },
        silent: true,
      })
      .then(function (result) {
        if (seq !== that.seq) return null
        var items = decorateRows((result && result.items) || [], page)
        var total = (result && result.total) || 0
        that.setData({
          loading: false,
          loadingMore: false,
          error: '',
          errorAction: '',
          items: items,
          total: total,
          hasMore: portal.nextPageState(page, PAGE_SIZE, total).hasMore,
        })
        return result
      })
      .catch(function (error) {
        if (seq !== that.seq) return null
        var described = portal.describePortalError(error)
        that.setData({
          loading: false,
          loadingMore: false,
          error: described.message,
          errorAction: described.action || '',
        })
        return null
      })
  },

  loadMore: function () {
    var that = this
    var page = this.page
    this.setData({ loadingMore: true })

    http
      .get('/me/children/' + this.studentId + '/feedback', {
        data: { page: page, page_size: PAGE_SIZE },
        silent: true,
      })
      .then(function (result) {
        var fresh = decorateRows((result && result.items) || [], page)
        var items = that.data.items.concat(fresh)
        var total = (result && result.total) || 0
        that.setData({
          items: items,
          loadingMore: false,
          total: total,
          hasMore: portal.nextPageState(page, PAGE_SIZE, total).hasMore,
        })
      })
      .catch(function (error) {
        var described = portal.describePortalError(error)
        that.page = page - 1
        that.setData({
          loadingMore: false,
          error: described.message,
          errorAction: described.action || '',
        })
      })
  },

  onRetry: function () {
    this.page = 1
    this.load()
  },

  goSwitch: function () {
    wx.navigateTo({ url: '/pages/switch-student/index' })
  },
})
