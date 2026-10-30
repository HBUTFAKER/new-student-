<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadFile } from 'element-plus'

import { ApiError } from '@/api/http'
import { downloadCsvTemplate, parseCsvFile } from '@/utils/csv'

const MAX_ROWS = 200

const props = defineProps<{
  modelValue: boolean
  title: string
  templateFileName: string
  templateHeaders: string[]
  templateSample: string[]
  /** 逐行提交；抛 ApiError 则记为该行失败，不中断后续行 */
  submitRow: (row: Record<string, string>) => Promise<void>
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  finished: []
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const fileName = ref('')
const rows = ref<Record<string, string>[]>([])
const parsing = ref(false)
const importing = ref(false)
const progress = ref(0)
const results = ref<{ line: number; ok: boolean; message: string }[]>([])

const successCount = computed(() => results.value.filter((item) => item.ok).length)
const failedCount = computed(() => results.value.filter((item) => !item.ok).length)
const overLimit = computed(() => rows.value.length > MAX_ROWS)

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    fileName.value = ''
    rows.value = []
    results.value = []
    progress.value = 0
    importing.value = false
  },
)

function handleTemplate(): void {
  downloadCsvTemplate(props.templateFileName, props.templateHeaders, props.templateSample)
}

async function handleFileChange(file: UploadFile): Promise<void> {
  const raw = file.raw
  if (!raw) return

  parsing.value = true
  results.value = []
  try {
    const parsed = await parseCsvFile(raw)
    fileName.value = raw.name
    rows.value = parsed.rows
    if (parsed.rows.length === 0) {
      ElMessage.warning('文件里没有可导入的数据行')
    } else if (parsed.rows.length > MAX_ROWS) {
      ElMessage.error(`单个文件最多 ${MAX_ROWS} 行，当前 ${parsed.rows.length} 行，请拆分后再导入`)
    }
  } catch (error) {
    fileName.value = ''
    rows.value = []
    ElMessage.error(error instanceof Error ? error.message : 'CSV 解析失败，请检查文件格式')
  } finally {
    parsing.value = false
  }
}

async function startImport(): Promise<void> {
  if (rows.value.length === 0) {
    ElMessage.warning('请先选择 CSV 文件')
    return
  }
  if (overLimit.value) {
    ElMessage.error(`单个文件最多 ${MAX_ROWS} 行，请拆分后再导入`)
    return
  }

  importing.value = true
  results.value = []
  progress.value = 0

  try {
    for (let index = 0; index < rows.value.length; index += 1) {
      const row = rows.value[index] ?? {}
      progress.value = index + 1
      try {
        await props.submitRow(row)
        results.value.push({ line: index + 2, ok: true, message: '成功' })
      } catch (error) {
        const message =
          error instanceof ApiError
            ? `${error.code} · ${error.message}`
            : error instanceof Error
              ? error.message
              : '未知错误'
        results.value.push({ line: index + 2, ok: false, message })
      }
    }

    ElMessage.success(`导入完成：成功 ${successCount.value} 条，失败 ${failedCount.value} 条`)
    emit('finished')
  } finally {
    importing.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" :title="title" width="560px" :close-on-click-modal="!importing">
    <div class="import">
      <div class="import__row">
        <el-button plain @click="handleTemplate">下载 CSV 模板</el-button>
        <span class="import__hint">表头：{{ templateHeaders.join(' / ') }}（单文件最多 200 行）</span>
      </div>

      <el-upload
        :auto-upload="false"
        :show-file-list="false"
        accept=".csv,text/csv"
        :on-change="handleFileChange"
        :disabled="importing"
      >
        <el-button type="primary" plain :loading="parsing">选择 CSV 文件</el-button>
      </el-upload>

      <div v-if="fileName" class="import__file">
        {{ fileName }}
        <span class="import__count">共 {{ rows.length }} 行</span>
      </div>

      <el-alert
        v-if="overLimit"
        type="error"
        show-icon
        :closable="false"
        :title="`超出单文件上限（200 行），当前 ${rows.length} 行，请拆分后重试`"
      />

      <el-progress
        v-if="importing || results.length > 0"
        :percentage="Math.round((progress / Math.max(rows.length, 1)) * 100)"
        :status="importing ? undefined : failedCount > 0 ? 'warning' : 'success'"
      />

      <div v-if="results.length > 0" class="import__results">
        <div class="import__summary">
          成功 <b>{{ successCount }}</b> 条 / 失败 <b class="import__failed">{{ failedCount }}</b> 条
        </div>
        <el-table :data="results" size="small" height="220" border>
          <el-table-column prop="line" label="行号" width="80" />
          <el-table-column label="结果" width="90">
            <template #default="{ row }">
              <el-tag :type="row.ok ? 'success' : 'danger'" size="small">
                {{ row.ok ? '成功' : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="message" label="说明" show-overflow-tooltip />
        </el-table>
      </div>
    </div>

    <template #footer>
      <el-button :disabled="importing" @click="visible = false">关闭</el-button>
      <el-button
        type="primary"
        :loading="importing"
        :disabled="rows.length === 0 || overLimit || (results.length > 0 && !importing)"
        @click="startImport"
      >
        {{ importing ? '导入中…' : results.length > 0 ? '已导入（换文件可再导）' : '开始导入' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.import {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.import__row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.import__hint {
  font-size: 12px;
  color: #909399;
}

.import__file {
  font-size: 13px;
  color: #606266;
}

.import__count {
  margin-left: 8px;
  color: #909399;
}

.import__summary {
  margin-bottom: 8px;
  font-size: 13px;
  color: #606266;
}

.import__failed {
  color: #f56c6c;
}
</style>
