<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const from = computed(() => (typeof route.query.from === 'string' ? route.query.from : ''))

function goHome(): void {
  router.replace(auth.role === 'platform_admin' ? '/platform/tenants' : '/dashboard')
}
</script>

<template>
  <div class="forbidden">
    <div class="forbidden__art" aria-hidden="true">
      <div class="forbidden__ring" />
      <div class="forbidden__slash" />
      <div class="forbidden__mark">!</div>
    </div>
    <h1 class="forbidden__title">403 · 无权访问</h1>
    <p class="forbidden__desc">当前账号没有进入该页面的权限，请联系机构管理员</p>
    <p v-if="from" class="forbidden__from">被拦截的地址：{{ from }}</p>
    <el-button type="primary" @click="goHome">返回后台</el-button>
  </div>
</template>

<style scoped>
.forbidden {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
  padding: var(--app-space-6);
  text-align: center;
}

.forbidden__art {
  position: relative;
  width: 148px;
  height: 148px;
  margin-bottom: var(--app-space-6);
}

.forbidden__ring {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: var(--el-color-primary-light-9);
}

.forbidden__slash {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  border: 10px solid transparent;
  border-right-color: var(--el-color-danger);
  transform: rotate(30deg);
}

.forbidden__mark {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 56px;
  font-weight: 700;
  color: var(--el-color-primary);
  line-height: 1;
}

.forbidden__title {
  margin: 0;
  font-size: 22px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.forbidden__desc {
  margin: var(--app-space-2) 0 0;
  font-size: 14px;
  color: var(--el-text-color-secondary);
}

.forbidden__from {
  margin: var(--app-space-2) 0 var(--app-space-4);
  font-size: 13px;
  color: var(--el-text-color-placeholder);
  word-break: break-all;
  max-width: 480px;
}
</style>
