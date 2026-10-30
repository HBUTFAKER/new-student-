<script setup lang="ts">
import { computed } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'

import { menuForRole } from '@/config/menu'
import { useAuthStore } from '@/stores/auth'
import { ROLE_LABELS } from '@/types/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const menuItems = computed(() => menuForRole(auth.role))
const roleLabel = computed(() => (auth.role ? ROLE_LABELS[auth.role] : ''))
const pageTitle = computed(() => (typeof route.meta.title === 'string' ? route.meta.title : ''))

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
  <el-container class="layout">
    <el-aside width="220px" class="layout__aside">
      <div class="layout__brand">
        <span class="layout__brand-mark">托</span>
        <span class="layout__brand-text">机构管理系统</span>
      </div>
      <el-menu :default-active="route.path" router class="layout__menu">
        <el-menu-item v-for="item in menuItems" :key="item.path" :index="item.path">
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.title }}</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="layout__header">
        <div class="layout__title">{{ pageTitle }}</div>
        <div class="layout__user">
          <el-tag size="small" type="info" effect="plain">{{ roleLabel }}</el-tag>
          <span class="layout__account">{{ auth.account }}</span>
          <el-button link type="primary" @click="handleSignOut">退出登录</el-button>
        </div>
      </el-header>

      <el-main class="layout__main">
        <RouterView />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout {
  height: 100%;
}

.layout__aside {
  display: flex;
  flex-direction: column;
  background-color: #ffffff;
  border-right: 1px solid #e4e7ed;
}

.layout__brand {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 60px;
  padding: 0 20px;
  border-bottom: 1px solid #e4e7ed;
}

.layout__brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background-color: #409eff;
  color: #ffffff;
  font-size: 15px;
  font-weight: 600;
}

.layout__brand-text {
  font-size: 15px;
  font-weight: 600;
}

.layout__menu {
  flex: 1;
  border-right: none;
}

.layout__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 60px;
  background-color: #ffffff;
  border-bottom: 1px solid #e4e7ed;
}

.layout__title {
  font-size: 16px;
  font-weight: 600;
}

.layout__user {
  display: flex;
  align-items: center;
  gap: 12px;
}

.layout__account {
  font-size: 13px;
  color: #606266;
}

.layout__main {
  padding: 20px;
  background-color: #f5f7fa;
}
</style>
