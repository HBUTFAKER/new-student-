/**
 * 小程序入口。
 * 只做全局初始化，不做跳转——首屏是登录页，它在 onLoad 里根据 token 决定去哪。
 */

'use strict'

var storage = require('./utils/storage')

App({
  globalData: {
    auth: null,
  },

  onLaunch: function () {
    this.globalData.auth = storage.getAuth()
  },
})
