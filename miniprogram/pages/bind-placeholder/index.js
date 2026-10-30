'use strict'

var http = require('../../utils/request')
var storage = require('../../utils/storage')

Page({
  data: {
    checking: false,
  },

  /**
   * 5.1 只做引导占位，真正的邀请码绑定是 5.2。
   * 「重新检查」留给家长在机构发码之后回来确认，也顺手给 5.2 留了入口钩子。
   */
  onCheck: function () {
    var that = this
    if (this.data.checking) return

    this.setData({ checking: true })

    http
      .get('/me/students', { silent: true })
      .then(function (result) {
        var students = (result && result.items) || []
        var auth = storage.getAuth() || {}
        that.setData({ checking: false })

        if (students.length > 0 && auth.tenant_id) {
          wx.reLaunch({ url: '/pages/home/index' })
          return
        }
        wx.showToast({ title: '还没有绑定记录', icon: 'none' })
      })
      .catch(function (error) {
        that.setData({ checking: false })
        wx.showToast({ title: (error && error.message) || '检查失败，请重试', icon: 'none' })
      })
  },

  onLogout: function () {
    storage.clearAuth()
    wx.reLaunch({ url: '/pages/login/index' })
  },
})
