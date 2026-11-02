<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'

import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

async function handleSignOut(): Promise<void> {
  try {
    await ElMessageBox.confirm('退出后需要重新登录，确认退出？', '退出登录', {
      confirmButtonText: '退出登录',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  auth.signOut()
  await router.replace({ name: 'login' })
}
</script>

<template>
  <div class="platform">
    <header class="platform__topbar">
      <div class="platform__brand">
        <span class="platform__mark">平</span>
        <span class="platform__brand-text">平台运营后台</span>
        <span class="platform__badge">PLATFORM</span>
      </div>
      <nav class="platform__nav">
        <RouterLink
          class="platform__nav-item"
          :class="{ 'is-active': route.path.startsWith('/platform/tenants') }"
          to="/platform/tenants"
        >
          租户管理
        </RouterLink>
      </nav>
      <div class="platform__user">
        <span class="platform__account">{{ auth.account }}</span>
        <el-button link class="platform__signout" @click="handleSignOut">退出登录</el-button>
      </div>
    </header>
    <main class="platform__main">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.platform {
  display: flex;
  flex-direction: column;
  height: 100%;
  background-color: var(--app-platform-page);
}

.platform__topbar {
  display: flex;
  align-items: center;
  gap: 32px;
  height: 60px;
  padding: 0 24px;
  background-color: var(--app-platform-bg);
  color: var(--app-platform-text);
  box-shadow: 0 1px 4px rgba(17, 24, 39, 0.2);
}

.platform__brand {
  display: flex;
  align-items: center;
  gap: 10px;
}

.platform__mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background-color: var(--app-platform-accent);
  color: var(--app-platform-bg);
  font-size: 15px;
  font-weight: 700;
}

.platform__brand-text {
  font-size: 15px;
  font-weight: 600;
  color: var(--app-platform-text-strong);
}

.platform__badge {
  padding: 2px 8px;
  border: 1px solid var(--app-platform-accent);
  border-radius: 999px;
  color: var(--app-platform-accent);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 1px;
}

.platform__nav {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 100%;
}

.platform__nav-item {
  display: inline-flex;
  align-items: center;
  height: 100%;
  padding: 0 16px;
  color: var(--app-platform-text-muted);
  font-size: 14px;
  text-decoration: none;
  border-bottom: 2px solid transparent;
}

.platform__nav-item:hover {
  color: var(--app-platform-text-strong);
}

.platform__nav-item.is-active {
  color: var(--app-platform-accent);
  border-bottom-color: var(--app-platform-accent);
  font-weight: 600;
}

.platform__user {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-left: auto;
}

.platform__account {
  font-size: 13px;
  color: var(--app-platform-text-muted);
}

.platform__signout {
  color: var(--app-platform-text-muted);
}

.platform__signout:hover {
  color: var(--app-platform-text-strong);
}

.platform__main {
  flex: 1;
  padding: 24px;
  overflow: auto;
}
</style>
