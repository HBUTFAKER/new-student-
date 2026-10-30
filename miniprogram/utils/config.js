/**
 * 环境与接口地址。
 *
 * 一期只有微信端，没有构建链，所以「切环境」靠 envVersion 自动判断：
 * 开发者工具 = develop、体验版 = trial、正式版 = release。
 * 真实部署时把 trial / release 换成自己的域名，并在小程序后台配 request 合法域名。
 */

'use strict'

var BASE_URLS = {
  // 本地联调：后端 uvicorn 跑在 8000。开发者工具需勾「不校验合法域名」
  develop: 'http://127.0.0.1:8000',
  // 体验版 / 正式版：部署时替换成真实 HTTPS 域名
  trial: 'https://api.example.com',
  release: 'https://api.example.com',
}

function detectEnv() {
  try {
    var info = wx.getAccountInfoSync()
    var version = info && info.miniProgram && info.miniProgram.envVersion
    if (version && BASE_URLS[version]) return version
  } catch (error) {
    // 开发者工具旧版本 / 非小程序环境：按开发态处理
  }
  return 'develop'
}

var ENV = detectEnv()

module.exports = {
  ENV: ENV,
  BASE_URL: BASE_URLS[ENV],
  BASE_URLS: BASE_URLS,
  REQUEST_TIMEOUT: 15000,

  /**
   * 开发态兜底：project.config.json 里 appid 是占位符 touristappid，游客模式下
   * wx.login() 拿不到 code，而后端 local/test 环境默认走 StubWechatAuthClient
   * （任意 wx_code 都能换到身份）。所以仅 develop 允许用固定假 code。
   * 换成真实 appid 后 envVersion 变为 trial/release，这条兜底自动失效。
   */
  ALLOW_FAKE_WX_CODE: ENV === 'develop',
  FAKE_WX_CODE: 'devtools_openid|devtools_unionid',
}
