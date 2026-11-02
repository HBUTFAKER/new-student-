# syntax=docker/dockerfile:1
# 管理端前端多阶段构建：node 构建 dist → nginx 静态托管 + 反代后端 API。
# 生产同源方案（026 裁决）：VITE_API_BASE_URL 留空，API 由 nginx 转发到 api 服务。

FROM node:20-slim AS build

# pnpm 版本由 web/package.json 的 packageManager 字段锁定（pnpm@12.14.0）。
RUN corepack enable

WORKDIR /build
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY web/ ./
RUN pnpm build

FROM nginx:1.27-alpine AS serve

COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html

EXPOSE 80
