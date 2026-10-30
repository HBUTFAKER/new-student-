<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { createInviteCode, listInviteCodes } from '@/api/archive'
import { ApiError } from '@/api/http'
import { INVITE_STATUS_LABELS } from '@/types/archive'
import type { InviteCode, Student } from '@/types/archive'
import { formatDateTime } from '@/utils/format'

/**
 * 学生邀请码管理：教务发码，家长在小程序里输码/扫码绑定。
 * 一期只出文本码，不生成二维码图片（031 禁令）。
 */
const props = defineProps<{ modelValue: boolean; student: Student | null }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const HOUR_OPTIONS = [
  { label: '24 小时', value: 24 },
  { label: '3 天', value: 72 },
  { label: '7 天', value: 168 },
]

const items = ref<InviteCode[]>([])
const loading = ref(false)
const generating = ref(false)
const hours = ref(24)
const latest = ref<InviteCode | null>(null)
const errorText = ref('')

async function load(): Promise<void> {
  if (!props.student) return
  loading.value = true
  errorText.value = ''
  try {
    const data = await listInviteCodes(props.student.id)
    items.value = data.items
  } catch (error) {
    errorText.value = error instanceof ApiError ? error.message : '加载失败'
    items.value = []
  } finally {
    loading.value = false
  }
}

watch(
  () => props.modelValue,
  (open: boolean) => {
    if (!open) return
    latest.value = null
    hours.value = 24
    errorText.value = ''
    void load()
  },
)

async function generate(): Promise<void> {
  if (!props.student) return
  generating.value = true
  try {
    latest.value = await createInviteCode({
      student_id: props.student.id,
      expires_in_hours: hours.value,
    })
    ElMessage.success('已生成邀请码')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '生成失败，请稍后重试')
  } finally {
    generating.value = false
  }
}

async function copyCode(code: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(code)
    ElMessage.success('已复制')
  } catch {
    // 非 HTTPS 或老浏览器没有 clipboard API，退回 textarea
    const area = document.createElement('textarea')
    area.value = code
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(area)
    if (ok) ElMessage.success('已复制')
    else ElMessage.warning('复制失败，请手动选中')
  }
}
</script>

<template>
  <el-dialog v-model="visible" :title="`邀请码 · ${student?.name ?? ''}`" width="620px">
    <div class="invite">
      <div class="invite__gen">
        <el-select v-model="hours" style="width: 130px">
          <el-option
            v-for="option in HOUR_OPTIONS"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-button type="primary" :loading="generating" @click="generate">生成新码</el-button>
        <span class="invite__hint">一次性使用，家长绑定后立即作废</span>
      </div>

      <div v-if="latest" class="invite__latest">
        <div class="invite__latest-label">新生成的邀请码</div>
        <div class="invite__code">{{ latest.code }}</div>
        <div class="invite__latest-foot">
          <el-button link type="primary" @click="copyCode(latest.code)">复制</el-button>
          <span class="invite__hint">有效期至 {{ formatDateTime(latest.expires_at) }}</span>
        </div>
      </div>

      <el-alert v-if="errorText" :title="errorText" type="error" :closable="false" show-icon />

      <el-table
        v-loading="loading"
        :data="items"
        size="small"
        empty-text="还没有生成过邀请码"
        class="invite__table"
      >
        <el-table-column label="邀请码" width="130">
          <template #default="{ row }">
            <span :class="{ 'invite__dim': row.status !== 'valid' }">{{ row.code }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'valid' ? 'success' : 'info'" size="small">
              {{ INVITE_STATUS_LABELS[row.status as InviteCode['status']] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="有效期至" min-width="160">
          <template #default="{ row }">
            <span :class="{ 'invite__dim': row.status !== 'valid' }">
              {{ formatDateTime(row.expires_at) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="使用人" width="100">
          <template #default="{ row }">
            <span class="invite__dim">{{ row.used_by ? row.used_by.slice(0, 8) : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button v-if="row.status === 'valid'" link type="primary" @click="copyCode(row.code)">
              复制
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </el-dialog>
</template>

<style scoped>
.invite__gen {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.invite__hint {
  color: #8a9099;
  font-size: 12px;
}

.invite__latest {
  margin-bottom: 16px;
  padding: 16px;
  border: 1px dashed #2f6fed;
  border-radius: 8px;
  background: #f5f8ff;
}

.invite__latest-label {
  color: #8a9099;
  font-size: 12px;
}

.invite__code {
  margin: 8px 0;
  color: #1f2329;
  font-size: 30px;
  font-weight: 600;
  font-family: 'SF Mono', Consolas, monospace;
  letter-spacing: 4px;
}

.invite__latest-foot {
  display: flex;
  align-items: center;
  gap: 12px;
}

.invite__table {
  margin-top: 12px;
}

.invite__dim {
  color: #a8adb5;
}
</style>
