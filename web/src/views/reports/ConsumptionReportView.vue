<script setup lang="ts">
/**
 * 报表 · 课耗（`GET /reports/consumption`）
 *
 * `items` 是学生维度的总数，`by_class` 是同一批流水的班级拆分（两者恒等）。
 * 班级分布放在展开行里，不占主表列——学生一多，一级表会被班级列撑爆。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { ApiError } from '@/api/http'
import { getConsumption } from '@/api/report'
import type { ClassConsumptionRow, ConsumptionReport } from '@/types/report'
import { exportCsv } from '@/utils/csv'
import {
  type DateRange,
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
const report = ref<ConsumptionReport | null>(null)

const rows = computed(() => report.value?.items ?? [])
const isEmpty = computed(() => !loading.value && !errorText.value && rows.value.length === 0)

/** student_id → 该生的班级分布，展开行 O(1) 取 */
const byClassMap = computed(() => {
  const map = new Map<string, ClassConsumptionRow[]>()
  for (const item of report.value?.by_class ?? []) {
    const list = map.get(item.student_id)
    if (list) list.push(item)
    else map.set(item.student_id, [item])
  }
  return map
})

function classRowsOf(studentId: string): ClassConsumptionRow[] {
  return byClassMap.value.get(studentId) ?? []
}

async function load(): Promise<void> {
  if (!isRangeValid(range.value)) {
    errorText.value = '开始日期不能晚于结束日期'
    return
  }
  loading.value = true
  errorText.value = ''
  try {
    report.value = await getConsumption(toWindowIso(range.value))
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
      reportFileName('课耗', range.value),
      [
        { key: 'student_name', label: '学生' },
        { key: 'total_consumed', label: '消耗课时' },
        { key: 'lesson_count', label: '课次' },
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

      <el-empty v-if="isEmpty" description="该时间范围内没有课时消耗流水" />

      <template v-else>
        <div class="page__meta">共 {{ report?.total ?? 0 }} 名学生 · 展开行可看班级分布</div>

        <el-table v-loading="loading" :data="rows" border stripe row-key="student_id">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="expand">
                <div class="expand__title">班级分布</div>
                <el-table
                  v-if="classRowsOf(row.student_id).length > 0"
                  :data="classRowsOf(row.student_id)"
                  size="small"
                  border
                >
                  <el-table-column label="班级" min-width="180">
                    <template #default="{ row: item }">
                      {{ item.class_name || '未关联班级' }}
                    </template>
                  </el-table-column>
                  <el-table-column prop="consumed" label="消耗课时" width="120" align="right" />
                  <el-table-column prop="lesson_count" label="课次" width="100" align="right" />
                </el-table>
                <div v-else class="expand__empty">该学生没有按班级的课耗明细</div>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="student_name" label="学生" min-width="160" />
          <el-table-column prop="total_consumed" label="消耗课时" width="120" align="right" />
          <el-table-column prop="lesson_count" label="课次" width="100" align="right" />
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

.expand {
  padding: 4px 16px 12px 48px;
}

.expand__title {
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: #606266;
}

.expand__empty {
  color: #909399;
  font-size: 13px;
}
</style>
