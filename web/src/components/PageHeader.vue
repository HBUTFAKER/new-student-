<script setup lang="ts">
/**
 * PageHeader（7.7 统一页面骨架）：页标题 + 副标题 + 右侧主操作区。
 *
 * 列表页统一套用，替换各页自写的标题区。标题靠左、操作区靠右，
 * 1366×768 下不换行；窄屏允许换行兜底。
 */
defineProps<{
  title: string
  subtitle?: string
}>()
</script>

<template>
  <div class="page-header">
    <div class="page-header__text">
      <h1 class="page-header__title">{{ title }}</h1>
      <p v-if="subtitle" class="page-header__subtitle">{{ subtitle }}</p>
    </div>
    <div class="page-header__actions">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--app-space-4);
  flex-wrap: wrap;
  margin-bottom: var(--app-space-4);
}

.page-header__text {
  min-width: 0;
}

.page-header__title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  line-height: 1.4;
  color: var(--el-text-color-primary);
}

.page-header__subtitle {
  margin: var(--app-space-1) 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.page-header__actions {
  display: flex;
  align-items: center;
  gap: var(--app-space-2);
  flex-shrink: 0;
}
</style>
