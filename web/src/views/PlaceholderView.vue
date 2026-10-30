<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const title = computed(() => (typeof route.meta.title === 'string' ? route.meta.title : '该页面'))
</script>

<template>
  <el-card shadow="never" class="placeholder">
    <el-empty :description="`${title} · 建设中`">
      <p class="placeholder__hint">本步骤（4.1）只交付骨架，业务页面按 4.2 / 4.3 / 4.4 逐步补齐。</p>
    </el-empty>
  </el-card>
</template>

<style scoped>
.placeholder {
  border-radius: 10px;
}

.placeholder__hint {
  margin: 0;
  font-size: 13px;
  color: #909399;
}
</style>
