'use strict'

/**
 * 首页 = 家长端工作台（032）。
 *
 * 顶部是当前孩子（点击 → 选择孩子页），下面三张入口卡：本周课表 / 课时余额 / 扣课明细。
 * 三页的数据都由后端按 token 里的激活孩子返回，这里**不传 student_id**（红线）。
 */

var http = require('../../utils/request')
var storage = require('../../utils/storage')
var portal = require('../../utils/portal')
var subscribeRunner = require('../../utils/subscribe-runner')

var ENTRIES = [
  {
    key: 'schedule',
    title: '本周课表',
    desc: '按天看课次，调课与补课有徽章',
    url: '/pages/schedule/index',
  },
  {
    key: 'balance',
    title: '课时余额',
    desc: '剩余课时与课时包到期日',
    url: '/pages/balance/index',
  },
  {
    key: 'ledger',
    title: '扣课明细',
    desc: '每一次增减课时的流水',
    url: '/pages/ledger/index',
  },
]

Page({
  data: {
    loading: true,
    error: '',
    errorAction: '',
    student: null,
    entries: ENTRIES,
  },

  onShow: function () {
    this.load(true)
  },

  onPullDownRefresh: function () {
    this.load(true).then(function () {
      wx.stopPullDownRefresh()
    })
  },

  load: function (silent) {
    var that = this
    if (this.pending) return this.pending
    if (!silent) this.setData({ loading: true, error: '' })

    this.pending = http
      .get('/me/students', { silent: true })
      .then(function (result) {
        var students = (result && result.items) || []
        var active = null
        var index
        for (index = 0; index < students.length; index += 1) {
          if (students[index].active) {
            active = students[index]
            break
          }
        }
        // 只有一个孩子时后端也会标 active；万一没标（token 过期），退回第一个
        if (!active && students.length === 1) active = students[0]

        that.setData({
          loading: false,
          error: '',
          errorAction: '',
          student: active
            ? Object.assign({}, active, {
                initial: (active.student_name || '?').slice(0, 1),
              })
            : null,
          bound: students.length,
        })
        // 有当前孩子才收授权：没选孩子时后端会 403，别白弹一次窗
        if (active) that.collectSubscribe()
        return students
      })
      .catch(function (error) {
        var described = portal.describePortalError(error)
        that.setData({
          loading: false,
          error: described.message,
          errorAction: described.action || '',
        })
        return []
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

  /**
   * 进首页后触发一次订阅授权收集（033）。
   *
   * 每个页面实例只试一次：本地标记会把问过的模板挡掉，真正防重弹靠
   * `utils/subscribe.js` 的版本化标记，这里只是省掉一次无意义的接口往返。
   * 全链路静默——弹不出来、上报失败都不影响家长看课表。
   */
  collectSubscribe: function () {
    if (this.subscribeTried) return
    this.subscribeTried = true
    subscribeRunner.collect().catch(function () {
      return []
    })
  },

  goNotify: function () {
    wx.navigateTo({ url: '/pages/notify/index' })
  },

  goSwitch: function () {
    wx.navigateTo({ url: '/pages/switch-student/index' })
  },

  goBind: function () {
    wx.navigateTo({ url: '/pages/bind/index' })
  },

  goEntry: function (event) {
    var url = event.currentTarget.dataset.url
    if (!url) return
    if (!this.data.student) {
      wx.showToast({ title: '先选择孩子', icon: 'none' })
      return
    }
    wx.navigateTo({ url: url })
  },

  onLogout: function () {
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
