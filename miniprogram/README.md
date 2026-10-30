# 家长端小程序（一期）

原生微信小程序 + 原生 JS，**零 npm 依赖、零构建链**。用微信开发者工具「导入项目」直接选这个目录（`miniprogram/`）就能跑。

对应 `docs/phase0/032-instruction`（5.3）：骨架 + 登录 + 绑定/切孩子（5.2）+ 课表 / 余额 / 扣课明细三页（5.3）。

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
    session.js               会话合并（保留 role / 回填 bound_students）+ 邀请码归一化 + 错误码映射
    portal.js                三页纯逻辑：固定 +08:00 的周窗口推算、时间格式化、按天分组、徽章、分页判定
    auth.js                  login / bindChild / switchStudent，三个都会重签 token
  pages/
    login/                   首屏；已有 token 直接 reLaunch；登录后按 needBind / needSwitch 分流
    home/                    工作台：当前孩子（点卡切孩子）+ 本周课表 / 课时余额 / 扣课明细三入口
    bind/                    输码 / 粘剪贴板 / 扫码 → POST /me/bindings
    switch-student/          多孩子（跨机构）选择，POST /me/active-student
    schedule/                周课表：按天分组、moved「已调」/ makeup「补课」徽章、翻周、下拉刷新
    balance/                 课时余额：大字 total（负数红字 + 欠课时提示）、包剩余与到期日
    ledger/                  扣课明细：类型徽章 + 增减课时，上拉加载更多、下拉刷新
  tests/
    request-core.test.js     node:test 单测（15 项）
    session.test.js          node:test 单测（22 项）
    portal.test.js           node:test 单测（20 项）
    live-smoke.js            真后端联调冒烟（手动跑，72 项）
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
# 纯逻辑单测（不依赖后端；031 依旧按要求没往 CI 里加小程序 job）
node --test miniprogram/tests/request-core.test.js miniprogram/tests/session.test.js miniprogram/tests/portal.test.js

# 真后端冒烟（要先起后端；脚本会自己建学生、自己发码绑定，不依赖特定种子数据）
MP_API_BASE=http://127.0.0.1:8000 node miniprogram/tests/live-smoke.js
```

`live-smoke.js` 自带前置造数：学生不够就用教务接口建，起始身份没绑定就现场发码绑一个；5.3 段还会现场建课程/班级/课次，并在**下一周的空档**里挑时段（同一库反复跑不会撞跨班冲突）。默认教务账号 `13000000001 / demo123456`（可用 `MP_ADMIN_PHONE` / `MP_ADMIN_PASSWORD` 覆盖），已绑定账号 `live_smoke_openid|live_smoke_unionid`（`MP_BOUND_CODE`）。

「跨机构切换」那条是软断言：库里只有单一机构时会打印 `skip`，不会假失败。要硬验就得造第二个租户 + 学生。

## 要人工确认的事（自动化覆盖不到）

- **微信开发者工具编译通过**（本机没装工具，这步只能人跑；已做等价静态校验：全部 js `node --check`、json 可解析、七个页面四件套齐全、WXML 标签栈闭合）。
- 真机预览：`wx.login` 拿真 code、`request` 域名校验、下拉刷新手感。
- 三个页面在 iOS / Android 上的排版（WXSS 刻意避开了 flex `gap`，怕安卓 X5 老内核不认）。

## 上线前清单

- [ ] `project.config.json` 的 `appid` 换成真实小程序 appid
- [ ] `utils/config.js` 里 `trial` / `release` 的 `BASE_URLS` 换成真实 **HTTPS** 域名
- [ ] 小程序后台 → 开发管理 → 服务器域名 → request 合法域名加该域名
- [ ] 确认 `ALLOW_FAKE_WX_CODE` 在正式包里不生效（它只认 `envVersion === 'develop'`）
- [ ] 后端 `WECHAT_APP_SECRET` 配好、`WECHAT_STUB_MODE=false`
