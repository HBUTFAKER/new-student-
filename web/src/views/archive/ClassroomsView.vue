<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'

import { createClassroom, listClassrooms, updateClassroom } from '@/api/archive'
import { ApiError } from '@/api/http'
import { usePagedList } from '@/composables/usePagedList'
import type { Classroom } from '@/types/archive'
import { exportCsv } from '@/utils/csv'
import { formatDateTime } from '@/utils/format'

const { items, total, page, pageSize, loading, errorText, load, fetchAll } = usePagedList(
  listClassrooms,
  10,
)

function handlePageChange(next: number): void {
  page.value = next
  void load()
}

function handleSizeChange(size: number): void {
  pageSize.value = size
  page.value = 1
  void load()
}

// ---------------------------------------------------------------- 表单

const dialogVisible = ref(false)
const submitting = ref(false)
const editingId = ref<string | null>(null)
const formRef = ref<FormInstance>()
const serverErrors = reactive<Record<string, string>>({})

const form = reactive<{ name: string; capacity: number | null }>({ name: '', capacity: 20 })

const rules: FormRules<typeof form> = {
  name: [
    { required: true, message: '请输入教室名称', trigger: 'blur' },
    { max: 64, message: '名称最多 64 个字符', trigger: 'blur' },
  ],
  capacity: [
    { required: true, message: '请输入容量', trigger: 'blur' },
    {
      validator: (_rule, value: number | null, callback) => {
        if (value === null || value === undefined) {
          callback(new Error('请输入容量'))
          return
        }
        if (!Number.isInteger(value) || value <= 0) {
          callback(new Error('容量必须是正整数'))
          return
        }
        if (value > 10000) {
          callback(new Error('容量不能超过 10000'))
          return
        }
        callback()
      },
      trigger: 'blur',
    },
  ],
}

function clearServerErrors(): void {
  for (const key of Object.keys(serverErrors)) delete serverErrors[key]
}

function openCreate(): void {
  editingId.value = null
  form.name = ''
  form.capacity = 20
  clearServerErrors()
  dialogVisible.value = true
}

function openEdit(row: Classroom): void {
  editingId.value = row.id
  form.name = row.name
  form.capacity = row.capacity
  clearServerErrors()
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  clearServerErrors()
  submitting.value = true
  try {
    const payload = { name: form.name.trim(), capacity: Number(form.capacity) }
    if (editingId.value) {
      await updateClassroom(editingId.value, payload)
      ElMessage.success('已保存')
    } else {
      await createClassroom(payload)
      ElMessage.success('已新建')
    }
    dialogVisible.value = false
    await load()
  } catch (error) {
    if (error instanceof ApiError && error.code === 'CAPACITY_INVALID') {
      serverErrors.capacity = error.message
      return
    }
    if (error instanceof ApiError && error.fieldErrors) {
      Object.assign(serverErrors, error.fieldErrors)
      return
    }
    ElMessage.error(error instanceof ApiError ? error.message : '保存失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}

// ---------------------------------------------------------------- 导出

const exporting = ref(false)

async function handleExport(): Promise<void> {
  exporting.value = true
  try {
    const rows = await fetchAll()
    exportCsv<Classroom>(
      `教室档案-${new Date().toISOString().slice(0, 10)}.csv`,
      [
        { key: 'name', label: '教室名称' },
        { key: 'capacity', label: '容量', format: (row) => String(row.capacity) },
        {
          key: 'available_slots',
          label: '可用时段数',
          format: (row) => String(row.available_slots?.length ?? 0),
        },
        { key: 'created_at', label: '创建时间', format: (row) => formatDateTime(row.created_at) },
      ],
      rows,
    )
    ElMessage.success(`已导出 ${rows.length} 条`)
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '导出失败')
  } finally {
    exporting.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <el-card shadow="never" class="page__card">
      <div class="page__head">
        <div class="page__actions">
          <el-button plain :loading="exporting" @click="handleExport">导出 CSV</el-button>
          <el-button type="primary" @click="openCreate">新建教室</el-button>
        </div>
      </div>

      <el-alert
        v-if="errorText"
        :title="errorText"
        type="error"
        show-icon
        :closable="false"
        class="page__error"
      />

      <el-table v-loading="loading" :data="items" border stripe empty-text="暂无教室">
        <el-table-column prop="name" label="教室名称" min-width="160" />
        <el-table-column prop="capacity" label="容量" width="100" />
        <el-table-column label="可用时段数" width="120">
          <template #default="{ row }">{{ row.available_slots?.length ?? 0 }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="page__pager">
        <el-pagination
          :current-page="page"
          :page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          background
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑教室' : '新建教室'" width="460px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="80px">
        <el-form-item label="名称" prop="name" :error="serverErrors.name">
          <el-input v-model="form.name" maxlength="64" placeholder="如 多媒体教室" @input="delete serverErrors.name" />
        </el-form-item>
        <el-form-item label="容量" prop="capacity" :error="serverErrors.capacity">
          <el-input-number v-model="form.capacity" :min="1" :max="10000" :step="1" style="width: 100%" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
      </template>
    </el-dialog>
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

.page__head {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  margin-bottom: 16px;
}

.page__actions {
  display: flex;
  gap: 8px;
}

.page__error {
  margin-bottom: 12px;
}

.page__pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
