'use strict'

var auth = require('../../utils/auth')
var session = require('../../utils/session')

Page({
  data: {
    code: '',
    submitting: false,
    error: '',
    errorAction: '',
  },

  onInput: function (event) {
    this.setData({ code: event.detail.value, error: '', errorAction: '' })
  },

  onPaste: function () {
    var that = this
    wx.getClipboardData({
      success: function (result) {
        var code = session.normalizeInviteCode(result && result.data)
        if (code.length !== 9) {
          wx.showToast({ title: '剪贴板里没有可用的邀请码', icon: 'none' })
          return
        }
        that.setData({ code: code, error: '', errorAction: '' })
      },
      fail: function () {
        wx.showToast({ title: '读取剪贴板失败，请手动输入', icon: 'none' })
      },
    })
  },

  onScan: function () {
    var that = this
    wx.scanCode({
      onlyFromCamera: false,
      scanType: ['qrCode'],
      success: function (result) {
        var code = session.extractInviteCode(result && result.result)
        if (code.length !== 9) {
          that.setData({ error: '没从二维码里认出邀请码，请手动输入', errorAction: '' })
          return
        }
        that.setData({ code: code, error: '', errorAction: '' })
      },
      fail: function () {
        // 用户点取消也会走 fail，不弹错误
      },
    })
  },

  onSubmit: function () {
    var that = this
    if (this.data.submitting) return

    var code = session.normalizeInviteCode(this.data.code)
    if (code.length !== 9) {
      this.setData({ error: '邀请码是 8 位字母数字，形如 AB12-CD34', errorAction: '' })
      return
    }

    this.setData({ submitting: true, error: '', errorAction: '' })

    auth
      .bindChild(code)
      .then(function () {
        // 绑定成功即离开本页，不做连续绑定循环
        wx.reLaunch({ url: '/pages/home/index' })
      })
      .catch(function (error) {
        var described = session.describeBindError(error)
        that.setData({
          submitting: false,
          error: described.message,
          errorAction: described.action || '',
        })
      })
  },

  goSwitch: function () {
    wx.reLaunch({ url: '/pages/switch-student/index' })
  },

  goLogin: function () {
    wx.reLaunch({ url: '/pages/login/index' })
  },
})
