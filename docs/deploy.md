# 部署文档（从零到上线）

本文件是生产部署的事实标准（设计文档无部署章节）。目标：拿到一台空 VPS，照着做就能上到公网。

- 部署形态：Docker Compose 三服务（`db` + `api` + `web`），web 是 Nginx，同源反代后端 API（后端零 CORS，026 裁决）。
- 范围：本文件只管「可部署物 + 步骤」。买服务器/域名/HTTPS 证书是运营动作，不绑定任何具体厂商。
- 微信/短信保持 stub（`WECHAT_STUB_MODE=true`），真实密钥不进仓库，切换点见 §8。

---

## 0. 上线 checklist（先看结论，做完逐条打勾）

- [ ] `JWT_SECRET_KEY` 已改成随机强值（不是 dev 默认值）
- [ ] `POSTGRES_PASSWORD` / `APP_DB_PASSWORD` 已改强密码
- [ ] `APP_ENV=production`、`DEBUG=false`
- [ ] 生产从未运行过 `scripts/seed_demo.py`（它只认 `APP_ENV=local`，天然跑不了）
- [ ] DB 未暴露宿主端口（宿主机器连不上 5432）
- [ ] 反向代理前缀全通（`/auth/login`、`/health` 等 200；未列前缀 404）
- [ ] 小程序后台「request 合法域名」填了 `https://你的域名`
- [ ] `WECHAT_STUB_MODE` 保持 `true`（真实微信接入走 §8 切换点）
- [ ] 备份 cron 已配（§7）

---

## 1. 准备 VPS + 域名 + DNS

1. 买一台 VPS（任意云厂商，Ubuntu 22.04 / Debian 12 均可），内存 ≥ 2G。
2. 注册一个域名，DNS 加一条 A 记录指向 VPS 公网 IP，例如：

   ```
   admin.example.com   A   1.2.3.4
   ```

3. 确认解析生效：`ping admin.example.com`（或 `nslookup`）。

> HTTPS 证书要求域名已解析到这台机器，所以先做 DNS，再做 §6。

---

## 2. 装 Docker

Ubuntu/Debian 一条命令装官方 Docker + Compose 插件：

```bash
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
# 验证
docker --version
docker compose version
```

---

## 3. clone 仓库 + 复制 env 模板改密钥

```bash
git clone <你的仓库地址> student-manager
cd student-manager

# 复制生产 env 模板，改真实值
cp deploy/env.prod.example .env.prod
```

编辑 `.env.prod`，**至少必改这几处**（生成密钥用 `openssl rand -hex 32`）：

| 变量 | 必改 | 说明 |
|------|------|------|
| `JWT_SECRET_KEY` | ✅ | JWT 签名密钥，生产不改会导致拒绝启动 |
| `POSTGRES_PASSWORD` | ✅ | 数据库 owner 密码 |
| `APP_DB_PASSWORD` | ✅ | 运行时 app_user 密码（RLS 靠它生效） |
| `PLATFORM_ADMIN_PHONE` | ✅ | 首个平台管理员登录手机号 |
| `PLATFORM_ADMIN_PASSWORD` | ✅ | 首个平台管理员密码 |
| `HTTP_PORT` | 可选 | 对外端口，默认 80 |

> `DATABASE_URL` / `MIGRATION_DATABASE_URL` 不用填，compose 会自动拼装指向 db 服务。

---

## 4. 启动

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

- `--build` 第一次会构建 api 镜像和前端镜像（要几分钟）。
- api 启动时会先 `alembic upgrade head`（自动建表）再起 uvicorn。
- 看状态：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
```

三个服务都应 `Up` / `healthy`。若 api 没起来，看日志：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml logs api
```

---

## 5. 初始化平台管理员（生产首启必跑）

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml exec -T api \
  python scripts/init_platform_admin.py
```

- 从 `.env.prod` 读 `PLATFORM_ADMIN_PHONE` / `PLATFORM_ADMIN_PASSWORD`，创建首个 `platform_admin`。
- 幂等：跑两遍第二遍会提示「already exists, skipped」。
- **禁止在生产跑 `scripts/seed_demo.py`**（它会塞演示机构，且只认 local 环境，本来也跑不起来）。

---

## 6. 浏览器验证（注册 → 登录 → 建学生 → 发通知 stub）

1. 浏览器打开 `http://admin.example.com`（或 `http://<公网IP>`），应看到管理端登录页。
2. 点「注册机构」，自助开通一个机构（试用版 14 天）→ 用机构管理员手机号+密码登录。
3. 登录后建一个学生，确认能保存、列表能看到。
4. 点一次「上课提醒/通知」相关动作（stub 模式），确认站内信/红点能出，无报错。
5. 用 `platform_admin` 账号登录，应落到平台后台 `/platform/tenants`，能看到刚才注册的机构。

---

## 7. HTTPS（只写 80/443 转发要点）

域名解析生效后上证书。以 certbot + nginx 为例（容器外宿主机装 nginx 做 TLS 终结，转发到本机 80）：

```bash
sudo apt install -y certbot python3-certbot-nginx nginx
# 先把域名 A 记录指到本机，再申请证书（会自动改 nginx 配置）
sudo certbot --nginx -d admin.example.com
```

要点：
- TLS 终结在宿主机 nginx（443），把流量 `proxy_pass http://127.0.0.1:80;` 转发给 compose 的 web 服务（宿主机 80 端口）。
- 也可以用云厂商的免费证书 + 负载均衡转发，效果相同——只要求「80/443 进来的流量最终到 web 容器的 80」。

> 本仓库不内置 TLS，避免把证书续期逻辑焊死在 compose 里；宿主机 nginx 或云 LB 任选其一。

---

## 8. 微信小程序上线要点

1. **合法域名**：小程序后台「开发管理 → 服务器域名」，把 `request 合法域名` 填成 `https://admin.example.com`（HTTPS，不能带端口）。
2. **stub 切换点**（真实接入时才动，不接不碰）：
   - `.env.prod` 里把 `WECHAT_STUB_MODE=false`，填 `WECHAT_APP_ID` / `WECHAT_APP_SECRET`；
   - 订阅消息模板填 `WECHAT_SUBSCRIBE_TEMPLATES`（JSON，template_id 从微信后台取）；
   - 服务号通道填 `WECHAT_MP_APP_ID` / `WECHAT_MP_APP_SECRET` / `WECHAT_MP_TEMPLATE_ID`；
   - 短信通道填 `SMS_PROVIDER` 并关闭 `SMS_STUB_MODE`。
3. 以上密钥只进 `.env.prod`（已 gitignore），**永不进仓库**。

---

## 9. 备份 cron

一天一份够 10 人机构。脚本在 `scripts/backup.sh`（`pg_dump -Fc` + 保留 N 份轮转）。

```bash
# 手动跑一次确认能出 dump 文件
./scripts/backup.sh

# 配 cron：每天凌晨 3 点备份，保留 14 份，落到 /data/backups
crontab -e
# 加一行（把 /path/to/student-manager 换成实际路径）：
0 3 * * * cd /path/to/student-manager && BACKUP_DIR=/data/backups KEEP=14 ./scripts/backup.sh >> /data/backups/backup.log 2>&1
```

恢复示例（把 `backup_XXX.dump` 换成实际文件）：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml exec -T \
  -e PGPASSWORD="$POSTGRES_PASSWORD" db \
  pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists < backups/backup_XXX.dump
```

> `--clean --if-exists` 会先删同表再恢复，适合整库回滚；更安全的做法是先恢复到空库验证再切。

---

## 附录 A：反向代理前缀清单（漏一个 = 线上 404）

Nginx 反代按 FastAPI 顶层路由前缀逐一列出（`deploy/nginx.conf`）：

```
/health /auth /me /staff /admin /platform
/students /parents /teachers /classrooms /bindings /invite-codes
/courses /classes /schedule-rules /tenant-holidays /lessons /makeup-lessons
/org-config /reports /purchases /ledger /refunds /transfers
/docs /redoc /openapi.json
```

**新增任何顶层路由前缀，必须同步加进 `deploy/nginx.conf` 的正则 location。**

---

## 附录 B：生产 DB 结构要点

- 生产库 = 迁移产物 `0015`（`alembic upgrade head` 自动生成，表数 28 / 业务层 21）。
- 双角色连接结构（RLS 生效前提，7.2/7.3 owner 口径）：
  - 运行时 `DATABASE_URL` → `app_user`（非超管，RLS 真正生效）；
  - 迁移/DDL `MIGRATION_DATABASE_URL` → owner（建表、建角色）；
  - 定时任务 worker 内部走 owner 连接（跨租户扫描队列）。
- 这两个 URL 在 `docker-compose.prod.yml` 里已拼好，别改成同一个超管账号，否则 RLS 形同虚设。
