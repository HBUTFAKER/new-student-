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
    <el-result icon="warning" title="403 · 无权访问" sub-title="当前账号没有进入该页面的权限">
      <template #extra>
        <p v-if="from" class="forbidden__from">被拦截的地址：{{ from }}</p>
        <el-button type="primary" @click="goHome">返回后台</el-button>
      </template>
    </el-result>
  </div>
</template>

<style scoped>
.forbidden {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
}

.forbidden__from {
  margin: 0 0 16px;
  font-size: 13px;
  color: #909399;
  word-break: break-all;
}
</style>
