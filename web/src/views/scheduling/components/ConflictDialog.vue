<script setup lang="ts">
import { computed } from 'vue'

import { CONFLICT_TYPE_LABELS } from '@/types/lesson'
import type { ConflictRow } from '@/types/lesson'
import { datePart, timePart } from '@/utils/timetable'

const props = defineProps<{
  modelValue: boolean
  conflicts: ConflictRow[]
  title?: string
}>()

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

function range(row: ConflictRow): string {
  return `${datePart(row.start_at)} ${timePart(row.start_at)}–${timePart(row.end_at)}`
}
</script>

<template>
  <el-dialog v-model="visible" :title="title || '时段冲突，未保存'" width="720px">
    <el-alert
      type="warning"
      show-icon
      :closable="false"
      title="该时段与下列课次重叠（老师 / 教室 / 学员任一项都算冲突），请换一个时段。"
      class="conflict__hint"
    />

    <el-table :data="conflicts" border stripe size="small" empty-text="无冲突">
      <el-table-column label="冲突维度" width="100">
        <template #default="{ row }">
          <el-tag size="small" type="warning" effect="plain">
            {{ CONFLICT_TYPE_LABELS[row.type as keyof typeof CONFLICT_TYPE_LABELS] || row.type }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="班级" min-width="140">
        <template #default="{ row }">{{ row.class_name || row.class_id }}</template>
      </el-table-column>
      <el-table-column label="老师" width="100">
        <template #default="{ row }">{{ row.teacher_name || '-' }}</template>
      </el-table-column>
      <el-table-column label="教室" width="120">
        <template #default="{ row }">{{ row.classroom_name || '-' }}</template>
      </el-table-column>
      <el-table-column label="时间段" width="180">
        <template #default="{ row }">{{ range(row) }}</template>
      </el-table-column>
      <el-table-column label="课次 ID" width="120">
        <template #default="{ row }">
          <span class="conflict__id">{{ row.lesson_id.slice(0, 8) }}</span>
        </template>
      </el-table-column>
    </el-table>

    <template #footer>
      <el-button type="primary" @click="visible = false">知道了</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.conflict__hint {
  margin-bottom: 12px;
}

.conflict__id {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: #909399;
}
</style>
