'use strict'

/**
 * 扣课明细（032 / 05 §9.5）。
 *
 * 上拉加载下一页（page+1），下拉刷新回到第一页。分页状态由 utils/portal.nextPageState
 * 按后端返回的 total 判定，不靠「这次返回不足 page_size」猜（末页刚好整除会猜错）。
 */

var http = require('../../utils/request')
var portal = require('../../utils/portal')

var PAGE_SIZE = 20

/** 流水行没有业务主键，列表 key 用「页码-行号」，翻页也不会重复 */
function decorateRows(rows, page) {
  return rows.map(function (row, index) {
    return Object.assign(portal.decorateLedger(row), { key: page + '-' + index })
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
      .get('/me/ledger', { data: { page: page, page_size: PAGE_SIZE }, silent: true })
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
      .get('/me/ledger', { data: { page: page, page_size: PAGE_SIZE }, silent: true })
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

  goSwitch: function () {
    wx.navigateTo({ url: '/pages/switch-student/index' })
  },
})
