<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <div class="not-found">
    <div class="not-found__art" aria-hidden="true">
      <div class="not-found__orbit not-found__orbit--outer" />
      <div class="not-found__orbit not-found__orbit--inner" />
      <div class="not-found__code">404</div>
    </div>
    <h1 class="not-found__title">页面不存在</h1>
    <p class="not-found__desc">地址可能输错了，或该页面已被移除</p>
    <el-button type="primary" @click="router.replace('/')">返回首页</el-button>
  </div>
</template>

<style scoped>
.not-found {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: var(--app-space-6);
  text-align: center;
  background: var(--el-bg-color-page);
}

.not-found__art {
  position: relative;
  width: 176px;
  height: 176px;
  margin-bottom: var(--app-space-6);
}

.not-found__orbit {
  position: absolute;
  border-radius: 50%;
}

.not-found__orbit--outer {
  inset: 0;
  background: var(--el-color-primary-light-9);
}

.not-found__orbit--inner {
  inset: 22px;
  border: 3px dashed var(--el-color-primary-light-5);
  animation: nf-spin 24s linear infinite;
}

.not-found__code {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 46px;
  font-weight: 700;
  color: var(--el-color-primary);
  letter-spacing: 2px;
}

.not-found__title {
  margin: 0;
  font-size: 22px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.not-found__desc {
  margin: var(--app-space-2) 0 var(--app-space-5);
  font-size: 14px;
  color: var(--el-text-color-secondary);
}

@keyframes nf-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
