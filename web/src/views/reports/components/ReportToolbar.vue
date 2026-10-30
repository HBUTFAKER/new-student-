<script setup lang="ts">
/**
 * 报表页共用工具栏：日期范围 + 快捷区间 + 导出。
 *
 * 日期范围只改 v-model，**不在这里发请求**——页面 watch 范围变化自行重查，
 * 这样三个 Tab 的取数逻辑各自独立，工具栏不掺业务。
 *
 * 快捷区间直接给 `YYYY-MM-DD` 字符串，不走 el-date-picker 的 shortcuts：
 * 它的回调要返回 `Date`，再由 dayjs 按**本机时区**格式化，机器时区不是 +08:00
 * 时会整体错一天。这里宁可多写三行。
 */
import { computed } from 'vue'

import {
  type DateRange,
  describeRange,
  lastDaysRange,
  monthRange,
  monthToDateRange,
} from '@/utils/report'

const props = defineProps<{
  modelValue: DateRange
  loading?: boolean
  exporting?: boolean
  exportLabel?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: DateRange]
  export: []
}>()

const PRESETS: { label: string; build: () => DateRange }[] = [
  { label: '本月', build: monthToDateRange },
  { label: '上月', build: () => monthRange(-1) },
  { label: '近 30 天', build: () => lastDaysRange(30) },
]

const pickerValue = computed<[string, string]>(() => [props.modelValue.start, props.modelValue.end])

/** 日期组件的 emit 类型很宽（Date / number / string 都可能有），这里窄化成字符串区间 */
function handlePicker(value: unknown): void {
  // clearable 已关掉；这里再兜一层，避免半截区间把接口打 422
  if (!Array.isArray(value) || value.length !== 2) return
  const [start, end] = value as unknown[]
  if (typeof start !== 'string' || typeof end !== 'string' || !start || !end) return
  emit('update:modelValue', { start, end })
}

function applyPreset(build: () => DateRange): void {
  emit('update:modelValue', build())
}
</script>

<template>
  <div class="toolbar">
    <div class="toolbar__row">
      <el-date-picker
        :model-value="pickerValue"
        type="daterange"
        value-format="YYYY-MM-DD"
        range-separator="至"
        start-placeholder="开始日期"
        end-placeholder="结束日期"
        :clearable="false"
        :disabled="loading"
        style="width: 260px"
        @update:model-value="handlePicker"
      />
      <el-button-group>
        <el-button
          v-for="preset in PRESETS"
          :key="preset.label"
          :disabled="loading"
          @click="applyPreset(preset.build)"
        >
          {{ preset.label }}
        </el-button>
      </el-button-group>

      <div class="toolbar__spacer" />
      <slot name="extra" />
      <el-button plain :loading="exporting" :disabled="loading" @click="emit('export')">
        {{ exportLabel ?? '导出 CSV' }}
      </el-button>
    </div>
    <div class="toolbar__hint">
      统计窗口 {{ describeRange(modelValue) }}（含首尾，按 +08:00 切日，左闭右开）
    </div>
  </div>
</template>

<style scoped>
.toolbar {
  margin-bottom: 16px;
}

.toolbar__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.toolbar__spacer {
  flex: 1;
}

.toolbar__hint {
  margin-top: 8px;
  color: #909399;
  font-size: 12px;
}
</style>
