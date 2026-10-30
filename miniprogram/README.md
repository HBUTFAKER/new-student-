# 家长端小程序（一期）

原生微信小程序 + 原生 JS，**零 npm 依赖、零构建链**。用微信开发者工具「导入项目」直接选这个目录（`miniprogram/`）就能跑。

对应 `docs/phase0/030-instruction`（5.1）：只做工程骨架、登录、请求封装、首页占位。绑定邀请码是 5.2，课表/余额/扣课明细是 5.3。

## 目录

```
miniprogram/
  app.js / app.json / app.wxss / sitemap.json
  project.config.json        appid = touristappid（部署时换成真实 appid）
  utils/
    config.js                接口地址 + 环境判断（develop/trial/release）+ 开发态假 code 兜底
    storage.js               登录态读写（只存 token 与身份上下文，不存 openid/unionid）
    request-core.js          请求管线纯逻辑：Bearer 注入、401 单航班续期、错误归一（可在 Node 里单测）
    request.js               wx.request 适配层，把上面那层接到小程序
    auth.js                  wx.login → /auth/wechat-login → 校验 /me/students
  pages/
    login/                   首屏；已有 token 直接 reLaunch 到首页
    home/                    /me/students 孩子列表 + 下拉刷新 + 空/错/正常三态
    bind-placeholder/        「请向机构索取邀请码」占位（5.2 才做真绑定）
  tests/
    request-core.test.js     node:test 单测（15 项）
    live-smoke.js            真后端联调冒烟（手动跑，26 项）
```

## 本地联调

1. 起后端。`local` / `test` 环境默认就是 **stub 微信客户端**（`app/services/wechat.py`），任意 `wx_code` 都能换到身份，不需要真 appid：

   ```bash
   uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

2. 开发者工具 → 导入项目 → 目录选 `miniprogram/` → AppID 选「测试号」或直接用配置里的 `touristappid`。
3. 详情 → 本地设置 → 勾上 **「不校验合法域名、web-view（业务域名）、TLS 版本以及 HTTPS 证书」**（`project.config.json` 里已写 `urlCheck: false`，但工具版本不同可能还要手动勾一次）。
4. 点「微信一键登录」。

> **`touristappid` 的坑**：游客模式下 `wx.login()` 拿不到 `code`。因为后端是 stub，`utils/config.js` 在 `develop` 环境允许用固定假 code（`devtools_openid|devtools_unionid`）兜底；换成真实 appid 后 `envVersion` 变成 `trial`/`release`，兜底自动失效，走真 code。

## 测试

```bash
# 纯逻辑单测（不依赖后端，CI 也能跑，但 5.1 按要求没往 CI 里加 job）
node --test miniprogram/tests/request-core.test.js

# 真后端冒烟（要先起后端，且库里得有一个已绑定的家长）
MP_API_BASE=http://127.0.0.1:8000 node miniprogram/tests/live-smoke.js
```

`live-smoke.js` 默认用 `live_smoke_openid|live_smoke_unionid` 当已绑定账号、`live_smoke_new_openid|...` 当无绑定账号，可用 `MP_BOUND_CODE` / `MP_EMPTY_CODE` 覆盖。造绑定关系最省事的办法是走 `POST /me/bindings`（教务发邀请码）。

## 要人工确认的事（自动化覆盖不到）

- **微信开发者工具编译通过**（本机没装工具，这步只能人跑）。
- 真机预览：`wx.login` 拿真 code、`request` 域名校验、下拉刷新手感。
- 三个页面在 iOS / Android 上的排版（WXSS 刻意避开了 flex `gap`，怕安卓 X5 老内核不认）。

## 上线前清单

- [ ] `project.config.json` 的 `appid` 换成真实小程序 appid
- [ ] `utils/config.js` 里 `trial` / `release` 的 `BASE_URLS` 换成真实 **HTTPS** 域名
- [ ] 小程序后台 → 开发管理 → 服务器域名 → request 合法域名加该域名
- [ ] 确认 `ALLOW_FAKE_WX_CODE` 在正式包里不生效（它只认 `envVersion === 'develop'`）
- [ ] 后端 `WECHAT_APP_SECRET` 配好、`WECHAT_STUB_MODE=false`
