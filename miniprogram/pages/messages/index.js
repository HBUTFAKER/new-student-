'use strict'

/**
 * 站内信（034 / 5.5）。
 *
 * 微信订阅消息关掉也照样能看到提醒：列表倒序、未读小圆点、点击单条置已读、
 * 顶部「全部已读」。红点数字来自 `GET /me/notifications/unread-count`，
 * 这里不轮询——进页面拉一次，标已读后本地减法。
 */

var http = require('../../utils/request')
var portal = require('../../utils/portal')
var messages = require('../../utils/messages')

var PAGE_SIZE = 20

Page({
  data: {
    loading: true,
    loadingMore: false,
    error: '',
    errorAction: '',
    items: [],
    total: 0,
    unread: 0,
    hasMore: false,
    marking: false,
  },

  onLoad: function () {
    this.page = 1
    this.load()
  },

  onPullDownRefresh: function () {
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
      .get('/me/notifications', { data: { page: page, page_size: PAGE_SIZE }, silent: true })
      .then(function (result) {
        if (seq !== that.seq) return null
        var items = messages.decorateList((result && result.items) || [])
        var total = (result && result.total) || 0
        that.setData({
          loading: false,
          loadingMore: false,
          error: '',
          errorAction: '',
          items: items,
          total: total,
          unread: (result && result.unread) || 0,
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
      .get('/me/notifications', { data: { page: page, page_size: PAGE_SIZE }, silent: true })
      .then(function (result) {
        var fresh = messages.decorateList((result && result.items) || [])
        var total = (result && result.total) || 0
        that.setData({
          items: that.data.items.concat(fresh),
          loadingMore: false,
          total: total,
          unread: (result && result.unread) || 0,
          hasMore: portal.nextPageState(page, PAGE_SIZE, total).hasMore,
        })
      })
      .catch(function (error) {
        var described = portal.describePortalError(error)
        that.page = page - 1 // 回退页码，下次上拉重试这一页
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

  /** 点一行：未读就上报已读，成功后再改本地状态（失败不假装已读） */
  tapItem: function (event) {
    var index = event.currentTarget.dataset.index
    var item = this.data.items[index]
    if (!item || item.read === true) return

    var that = this
    http
      .post('/me/notifications/read', { data: { ids: [item.id] }, silent: true })
      .then(function () {
        var next = messages.markOneRead(that.data.items, item.id, that.data.unread)
        that.setData({ items: next.items, unread: next.unread })
      })
      .catch(function () {
        wx.showToast({ title: '网络不稳，稍后再试', icon: 'none' })
      })
  },

  markAll: function () {
    if (this.data.marking || this.data.unread === 0) return
    var that = this
    this.setData({ marking: true })
    http
      .post('/me/notifications/read', { data: { ids: [] }, silent: true })
      .then(function () {
        var next = messages.markAllRead(that.data.items)
        that.setData({ items: next.items, unread: next.unread, marking: false })
      })
      .catch(function () {
        that.setData({ marking: false })
        wx.showToast({ title: '网络不稳，稍后再试', icon: 'none' })
      })
  },

  goSwitch: function () {
    wx.navigateTo({ url: '/pages/switch-student/index' })
  },

  goHome: function () {
    wx.navigateBack()
  },
})
