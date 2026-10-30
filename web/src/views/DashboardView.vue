<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'

import { menuForRole } from '@/config/menu'
import { useAuthStore } from '@/stores/auth'
import { ROLE_LABELS } from '@/types/auth'

const auth = useAuthStore()
const router = useRouter()

const roleLabel = computed(() => (auth.role ? ROLE_LABELS[auth.role] : ''))
const menuItems = computed(() => menuForRole(auth.role))
</script>

<template>
  <div class="dashboard">
    <el-card shadow="never" class="dashboard__card">
      <div class="dashboard__greeting">
        <h2 class="dashboard__hello">欢迎回来，{{ auth.account }}</h2>
        <el-tag type="info" effect="plain">{{ roleLabel }}</el-tag>
      </div>
      <p class="dashboard__desc">
        当前是管理端骨架（4.1）：登录、角色菜单、路由守卫已可用。业务页面会在后续步骤逐步上线。
      </p>
    </el-card>

    <el-card shadow="never" class="dashboard__card">
      <template #header>
        <span class="dashboard__card-title">当前角色可进入的页面</span>
      </template>
      <el-space wrap :size="10">
        <el-button
          v-for="item in menuItems"
          :key="item.path"
          plain
          @click="router.push(item.path)"
        >
          {{ item.title }}
        </el-button>
      </el-space>
    </el-card>

    <el-alert
      title="越权访问会被拦截"
      type="info"
      show-icon
      :closable="false"
      description="直接在地址栏输入不属于当前角色的路由，会被守卫拦到 403 页，页面不会渲染。"
    />
  </div>
</template>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.dashboard__card {
  border-radius: 10px;
}

.dashboard__greeting {
  display: flex;
  align-items: center;
  gap: 12px;
}

.dashboard__hello {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}

.dashboard__desc {
  margin: 12px 0 0;
  font-size: 14px;
  line-height: 1.7;
  color: #606266;
}

.dashboard__card-title {
  font-size: 14px;
  font-weight: 600;
}
</style>
