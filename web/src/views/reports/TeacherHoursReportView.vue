<script setup lang="ts">
/**
 * 报表 · 老师课时（`GET /reports/teacher-hours`）
 *
 * `total_minutes` 是课次 `end_at - start_at` 的实际差值（不是课程配置的时长），
 * 前端只做分钟→「Xh Ym」的展示转换。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { ApiError } from '@/api/http'
import { getTeacherHours } from '@/api/report'
import type { TeacherHoursReport } from '@/types/report'
import { exportCsv } from '@/utils/csv'
import {
  type DateRange,
  formatMinutes,
  isRangeValid,
  monthToDateRange,
  reportFileName,
  toWindowIso,
} from '@/utils/report'

import ReportToolbar from './components/ReportToolbar.vue'

const range = ref<DateRange>(monthToDateRange())

const loading = ref(false)
const exporting = ref(false)
const errorText = ref('')
const report = ref<TeacherHoursReport | null>(null)

const rows = computed(() => report.value?.items ?? [])
const isEmpty = computed(() => !loading.value && !errorText.value && rows.value.length === 0)
const totalMinutes = computed(() => rows.value.reduce((sum, row) => sum + row.total_minutes, 0))

async function load(): Promise<void> {
  if (!isRangeValid(range.value)) {
    errorText.value = '开始日期不能晚于结束日期'
    return
  }
  loading.value = true
  errorText.value = ''
  try {
    report.value = await getTeacherHours(toWindowIso(range.value))
  } catch (error) {
    report.value = null
    errorText.value = error instanceof ApiError ? error.message : '报表加载失败'
  } finally {
    loading.value = false
  }
}

function handleExport(): void {
  if (rows.value.length === 0) {
    ElMessage.warning('当前筛选没有可导出的数据')
    return
  }
  exporting.value = true
  try {
    exportCsv(
      reportFileName('老师课时', range.value),
      [
        { key: 'teacher_name', label: '老师' },
        { key: 'lesson_count', label: '课次数' },
        { key: 'completed_count', label: '已完成节数' },
        { key: 'total_minutes', label: '总时长', format: (row) => formatMinutes(row.total_minutes) },
      ],
      rows.value,
    )
    ElMessage.success(`已导出 ${rows.value.length} 行`)
  } finally {
    exporting.value = false
  }
}

watch(range, () => void load(), { deep: true })

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <el-card shadow="never" class="page__card">
      <ReportToolbar v-model="range" :loading="loading" :exporting="exporting" @export="handleExport" />

      <el-alert
        v-if="errorText"
        :title="errorText"
        type="error"
        show-icon
        :closable="false"
        class="page__error"
      />

      <el-empty v-if="isEmpty" description="该时间范围内没有已排的课次" />

      <template v-else>
        <div class="page__meta">
          共 {{ report?.total ?? 0 }} 位老师 · 总时长 {{ formatMinutes(totalMinutes) }}
        </div>

        <el-table v-loading="loading" :data="rows" border stripe row-key="teacher_id">
          <el-table-column prop="teacher_name" label="老师" min-width="160" />
          <el-table-column prop="lesson_count" label="课次数" width="110" align="right" />
          <el-table-column prop="completed_count" label="已完成节数" width="130" align="right" />
          <el-table-column label="总时长" width="130" align="right">
            <template #default="{ row }">
              <span class="duration">{{ formatMinutes(row.total_minutes) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-card>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page__card {
  border-radius: 10px;
}

.page__error {
  margin-bottom: 12px;
}

.page__meta {
  color: #909399;
  font-size: 13px;
  margin-bottom: 8px;
}

.duration {
  font-variant-numeric: tabular-nums;
}
</style>
