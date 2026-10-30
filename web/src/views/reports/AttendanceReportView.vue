<script setup lang="ts">
/**
 * 报表 · 出勤率（`GET /reports/attendance-summary`）
 *
 * 三条口径来自 08 §0 [4.4 上]，前端只照抄、不重算：
 * - `attendance_rate === null` 显示「—」——「没数据」和「0% 出勤」是两个事实；
 * - `notes` 非空用 el-alert 摆在表格上方（makeup 预登记说明）；
 * - `student_rows` 只在 `detail=true` 时有内容，关掉就是空数组。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { listClasses } from '@/api/course'
import { ApiError } from '@/api/http'
import { getAttendanceSummary } from '@/api/report'
import type { ClassItem } from '@/types/course'
import type { AttendanceSummary } from '@/types/report'
import { exportCsv } from '@/utils/csv'
import {
  type DateRange,
  formatRate,
  isRangeValid,
  monthToDateRange,
  reportFileName,
  toWindowIso,
} from '@/utils/report'

import ReportToolbar from './components/ReportToolbar.vue'

const range = ref<DateRange>(monthToDateRange())
const classId = ref('')
const detail = ref(false)

const loading = ref(false)
const exporting = ref(false)
const errorText = ref('')
const summary = ref<AttendanceSummary | null>(null)
const classes = ref<ClassItem[]>([])

const rows = computed(() => summary.value?.items ?? [])
const studentRows = computed(() => summary.value?.student_rows ?? [])
const notes = computed(() => summary.value?.notes ?? [])
const isEmpty = computed(() => !loading.value && !errorText.value && rows.value.length === 0)

async function load(): Promise<void> {
  if (!isRangeValid(range.value)) {
    errorText.value = '开始日期不能晚于结束日期'
    return
  }
  loading.value = true
  errorText.value = ''
  try {
    summary.value = await getAttendanceSummary({
      ...toWindowIso(range.value),
      class_id: classId.value || undefined,
      detail: detail.value,
    })
  } catch (error) {
    // 拦截器已弹过提示，这里只留内联说明，不再二次弹窗
    summary.value = null
    errorText.value = error instanceof ApiError ? error.message : '报表加载失败'
  } finally {
    loading.value = false
  }
}

async function loadClasses(): Promise<void> {
  try {
    classes.value = (await listClasses({ page: 1, page_size: 100 })).items
  } catch {
    // 班级下拉失败不影响报表本身，静默降级为「全部班级」
    classes.value = []
  }
}

/** 导出当前正在看的那张表：开了展开导学生明细，没开导班级汇总 */
function handleExport(): void {
  if (detail.value) {
    if (studentRows.value.length === 0) {
      ElMessage.warning('当前筛选没有学生明细可导出')
      return
    }
    exporting.value = true
    try {
      exportCsv(
        reportFileName('出勤率-学生明细', range.value),
        [
          { key: 'student_name', label: '学生' },
          { key: 'present', label: '到课' },
          { key: 'leave', label: '请假' },
          { key: 'absent', label: '缺勤' },
          { key: 'attendance_rate', label: '出勤率', format: (row) => formatRate(row.attendance_rate) },
        ],
        studentRows.value,
      )
      ElMessage.success(`已导出 ${studentRows.value.length} 行学生明细`)
    } finally {
      exporting.value = false
    }
    return
  }

  if (rows.value.length === 0) {
    ElMessage.warning('当前筛选没有可导出的数据')
    return
  }
  exporting.value = true
  try {
    exportCsv(
      reportFileName('出勤率', range.value),
      [
        { key: 'class_name', label: '班级' },
        { key: 'lesson_count', label: '课次数' },
        { key: 'present', label: '到课' },
        { key: 'leave', label: '请假' },
        { key: 'absent', label: '缺勤' },
        { key: 'attendance_rate', label: '出勤率', format: (row) => formatRate(row.attendance_rate) },
      ],
      rows.value,
    )
    ElMessage.success(`已导出 ${rows.value.length} 行`)
  } finally {
    exporting.value = false
  }
}

watch([range, classId, detail], () => void load(), { deep: true })

onMounted(() => {
  void load()
  void loadClasses()
})
</script>

<template>
  <div class="page">
    <el-card shadow="never" class="page__card">
      <ReportToolbar
        v-model="range"
        :loading="loading"
        :exporting="exporting"
        :export-label="detail ? '导出学生明细' : '导出 CSV'"
        @export="handleExport"
      >
        <template #extra>
          <el-select
            v-model="classId"
            placeholder="全部班级"
            clearable
            :disabled="loading"
            style="width: 160px"
          >
            <el-option v-for="item in classes" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
          <el-switch v-model="detail" active-text="按学生展开" :disabled="loading" />
        </template>
      </ReportToolbar>

      <el-alert
        v-for="note in notes"
        :key="note"
        :title="note"
        type="warning"
        show-icon
        :closable="false"
        class="page__note"
      />

      <el-alert
        v-if="errorText"
        :title="errorText"
        type="error"
        show-icon
        :closable="false"
        class="page__error"
      />

      <el-empty v-if="isEmpty" description="该时间范围内没有已开始的课次" />

      <template v-else>
        <div class="page__meta">共 {{ summary?.total ?? 0 }} 个班级</div>

        <el-table v-loading="loading" :data="rows" border stripe row-key="class_id">
          <el-table-column prop="class_name" label="班级" min-width="160" />
          <el-table-column prop="lesson_count" label="课次数" width="100" align="right" />
          <el-table-column prop="present" label="到课" width="90" align="right" />
          <el-table-column prop="leave" label="请假" width="90" align="right" />
          <el-table-column prop="absent" label="缺勤" width="90" align="right" />
          <el-table-column label="出勤率" width="110" align="right">
            <template #default="{ row }">
              <span class="rate" :class="{ 'rate--empty': row.attendance_rate === null }">
                {{ formatRate(row.attendance_rate) }}
              </span>
            </template>
          </el-table-column>
        </el-table>

        <template v-if="detail">
          <div class="page__subtitle">按学生展开（{{ studentRows.length }} 人）</div>
          <el-table
            v-if="studentRows.length > 0"
            :data="studentRows"
            border
            stripe
            row-key="student_id"
            class="page__subtable"
          >
            <el-table-column prop="student_name" label="学生" min-width="160" />
            <el-table-column prop="present" label="到课" width="90" align="right" />
            <el-table-column prop="leave" label="请假" width="90" align="right" />
            <el-table-column prop="absent" label="缺勤" width="90" align="right" />
            <el-table-column label="出勤率" width="110" align="right">
              <template #default="{ row }">
                <span class="rate" :class="{ 'rate--empty': row.attendance_rate === null }">
                  {{ formatRate(row.attendance_rate) }}
                </span>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="该窗口内没有可统计的考勤记录" :image-size="60" />
        </template>
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

.page__note,
.page__error {
  margin-bottom: 12px;
}

.page__meta {
  color: #909399;
  font-size: 13px;
  margin-bottom: 8px;
}

.page__subtitle {
  margin: 20px 0 8px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.page__subtable {
  width: 100%;
}

.rate {
  font-variant-numeric: tabular-nums;
}

/* 「—」是没数据，不是 0%，视觉上也要轻一点 */
.rate--empty {
  color: #c0c4cc;
}
</style>
