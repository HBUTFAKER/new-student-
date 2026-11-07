# 小程序上线检查清单（049 · 一期收尾）

> 状态：家长端小程序**尚不可上线**。`miniprogram/utils/config.js` 的 trial/release `BASE_URL` 是占位符 `https://api.example.com`，`project.config.json` 的 appid 是 `touristappid`。真实值由机构方（瑞哥）上线时提供，本文把流程写成可勾选清单。**本文只新增文档，不动代码。**

---

## 一、API 域名（`miniprogram/utils/config.js`）

- [ ] 确认生产后端对外 HTTPS 域名已就绪（同源反代后的 Nginx 域名，见 `docs/deploy.md`）。
- [ ] 修改 `miniprogram/utils/config.js` 的 `BASE_URLS.trial` 与 `BASE_URLS.release` 为真实 HTTPS 域名（形如 `https://api.你的机构.com`，**不要**带末尾 `/`、**不要**用 HTTP）。
- [ ] 登录微信公众平台（mp.weixin.qq.com）→「开发」→「开发管理」→「开发设置」→「服务器域名」→「request 合法域名」，把上述域名加入白名单（要求：已备案、HTTPS、与 `BASE_URLS` 完全一致）。
- [ ] 确认该域名的 SSL 证书有效且未过期（小程序 request 只认 HTTPS + 合法域名）。
- [ ] 本地开发态 `develop: 'http://127.0.0.1:8000'` 保持不变；开发者工具联调时勾选「不校验合法域名」。

## 二、小程序 AppID（`miniprogram/project.config.json`）

- [ ] 登录微信公众平台拿到正式小程序 AppID（非 `touristappid`）。
- [ ] 把 `miniprogram/project.config.json` 的 `"appid": "touristappid"` 改为真实 AppID（或在微信开发者工具「详情 → 基本信息」里填写，会同步写回本文件）。
- [ ] 知晓副作用：换成真实 AppID 后，`envVersion` 变为 `trial`/`release`，`config.js` 里 `ALLOW_FAKE_WX_CODE = ENV === 'develop'` 会**自动失效**——即开发态那条固定假 `wx_code` 兜底不再生效，真实登录走 `jscode2session`（见下方第 3 点，后端必须同步切出 stub）。

## 三、生产环境变量（`deploy/env.prod.example` → `.env.prod`）

> 对照清单逐项核对，真实接入微信/短信时把 stub 关掉并填真实值。

- [ ] `WECHAT_STUB_MODE=false`（由默认 `true` 改为 `false`，接入真实微信登录）。
- [ ] `WECHAT_APP_ID`：填小程序 AppID。
- [ ] `WECHAT_APP_SECRET`：填小程序 AppSecret（**勿提交仓库**，走 `--env-file .env.prod` 注入）。
- [ ] `WECHAT_SUBSCRIBE_TEMPLATES`：从微信后台取订阅消息模板 id 后覆写（形如 `[{"template_id":"REAL_ID","type":"one_time","scene":"lesson_remind"}, ...]`），桩模式的内置默认仅供联调。
- [ ] 服务号 `wechat_mp_*`：`WECHAT_MP_APP_ID` / `WECHAT_MP_APP_SECRET` / `WECHAT_MP_TEMPLATE_ID`——留空 = 机构未配置 → 服务号通道静默跳过；要用则填真实值。
- [ ] 短信 `sms_provider`：`SMS_PROVIDER` 填真实供应商标识、`SMS_STUB_MODE=false`；留空 = 未配置 → 短信通道静默跳过。
- [ ] `APP_ENV=production`、`JWT_SECRET_KEY` 已填（`openssl rand -hex 32` 生成）、两处数据库密码（`POSTGRES_PASSWORD` / `APP_DB_PASSWORD`）已填。
- [ ] 平台管理员 `PLATFORM_ADMIN_PHONE` / `PLATFORM_ADMIN_PASSWORD` 已填（生产首启跑 `scripts/init_platform_admin.py`）。

## 四、扩容前必读（调度器单实例）

- [ ] 知晓：API 进程内自写 asyncio 调度器（上课提醒 + 余额预警 + 通知 worker）为**单实例设计**，生产 `SCHEDULER_ENABLED=true` 时**只能跑一个 API 实例**。
- [ ] 扩容到多实例前，必须先做分布式锁或把调度器拆成独立 worker（详见 `docs/phase0/08-implementation-delta.md` §0 与 §4 的架构债备忘）——否则定时任务会重复执行、重复发通知。

---

**验收**：本清单覆盖上述四块每一小项，只新增本文件，零代码改动。
