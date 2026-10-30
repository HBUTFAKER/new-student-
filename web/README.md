# 管理端 Web（web/）

托管机构管理端前端。4.1 只交付骨架：登录、角色菜单、路由守卫、Axios 封装；业务页面（4.2 / 4.3 / 4.4）后续补齐。

## 技术栈

Vue 3 + TypeScript + Vite + Element Plus + Vue Router + Pinia，包管理 pnpm（lockfile 已提交）。

## 命令

```bash
cd web
pnpm install
pnpm dev        # http://localhost:5173
pnpm build      # vue-tsc --noEmit && vite build
pnpm typecheck  # 只跑类型检查
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `VITE_API_BASE_URL` | 后端 API 根地址。`web/.env.development` 是 `/api`（走 Vite 同源代理，避免后端无 CORS 时的跨域拦截）；生产留空表示同源，由反向代理把 `/auth`、`/students` 等转发到后端 |
| `VITE_PROXY_TARGET` | 仅开发用，代理目标，默认 `http://localhost:8000` |

## 目录

```
src/
  api/        tokens.ts（内存+localStorage 双存）、http.ts（Bearer / 401 单航班续期 / 统一错误）、auth.ts
  stores/     auth.ts：登录态、角色、退出
  router/     index.ts：路由表（meta.roles 标角色）+ 全局守卫
  config/     menu.ts：按角色的菜单（教务/管理员 7 项，老师 4 项）
  layouts/    AdminLayout.vue：左菜单 + 顶栏 + 主内容区
  views/      LoginView / DashboardView / PlaceholderView / ForbiddenView / NotFoundView
```

## 登录态与守卫口径

- token：`access_token` 内存 + localStorage 双存，刷新页面登录态不丢；角色与账号存 `wb.profile`。
- 401：拦截器用 refresh_token 续期并重放原请求；并发多个 401 只发一次 refresh（单航班）。
- refresh 也失效：清 token → 回登录页，并带上 `?redirect=` 保住原目标。
- 守卫：未登录 → `/login?redirect=<原地址>`；已登录但角色不在 `meta.roles` → `/403`，目标页面组件不渲染。
- `?redirect=` 只接受站内相对路径，`//` 开头一律忽略（防开放重定向）。
