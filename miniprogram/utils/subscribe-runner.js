'use strict'

/**
 * 订阅授权的 wx 侧粘合层（033 / 5.4）。
 *
 * 纯逻辑全在 `utils/subscribe.js`；这里只做「读模板 → 弹窗 → 上报 → 落标记」的编排。
 * 整体失败静默：授权收集不能挡住家长正常用，弹不出来就下次再说。
 */

var http = require('./request')
var subscribe = require('./subscribe')

/** 给纯逻辑用的 storage 适配器（小程序里就是 wx.getStorageSync/setStorageSync） */
var store = {
  get: function (key) {
    try {
      return wx.getStorageSync(key) || null
    } catch (error) {
      return null
    }
  },
  set: function (key, value) {
    try {
      wx.setStorageSync(key, value)
    } catch (error) {
      // ignore
    }
  },
}

function askBatch(ids) {
  return new Promise(function (resolve) {
    if (typeof wx.requestSubscribeMessage !== 'function') {
      resolve([])
      return
    }
    wx.requestSubscribeMessage({
      tmplIds: ids,
      success: function (response) {
        resolve(subscribe.parseWxResult(ids, response))
      },
      fail: function () {
        // 用户直接关掉 / 基础库不支持：不给结果，也就不记账
        resolve([])
      },
    })
  })
}

var running = null

/**
 * 触发一次授权收集。同一时刻只跑一轮（首页 onShow + 手动点很容易撞）。
 * 返回 `Promise<results>`，results 是本次真正拿到结果的模板。
 */
function collect(options) {
  if (running) return running
  var force = !!(options && options.force)

  running = http
    .get('/me/subscribe-templates', { silent: true })
    .then(function (payload) {
      var templates = subscribe.normalizeTemplates(payload)
      var state = subscribe.readState(store)
      var plan = subscribe.batches(templates, state, { force: force })
      if (!plan.length) return []

      return plan
        .reduce(function (chain, ids) {
          return chain.then(function (collected) {
            return askBatch(ids).then(function (results) {
              return collected.concat(results)
            })
          })
        }, Promise.resolve([]))
        .then(function (results) {
          if (!results.length) return []
          subscribe.writeState(store, subscribe.mergeResults(state, results))
          return http
            .post('/me/subscribe-auths', { results: results }, { silent: true })
            .catch(function () {
              return null
            })
            .then(function () {
              return results
            })
        })
    })
    .catch(function () {
      return []
    })
    .then(function (results) {
      running = null
      return results || []
    })

  return running
}

/** 「通知设置」页用：模板 + 本地标记，纯读不发请求以外的事 */
function snapshot() {
  return http.get('/me/subscribe-templates', { silent: true }).then(function (payload) {
    var templates = subscribe.normalizeTemplates(payload)
    var state = subscribe.readState(store)
    return {
      templates: templates,
      rows: subscribe.summarize(templates, state),
      supported: typeof wx.requestSubscribeMessage === 'function',
    }
  })
}

module.exports = {
  collect: collect,
  snapshot: snapshot,
  store: store,
}
