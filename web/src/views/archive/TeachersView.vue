<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'

import { createTeacher, listTeachers, updateTeacher } from '@/api/archive'
import { ApiError } from '@/api/http'
import { usePagedList } from '@/composables/usePagedList'
import { TEACHER_STATUS_LABELS } from '@/types/archive'
import type { Teacher, TeacherPayload, TeacherStatus } from '@/types/archive'
import { exportCsv } from '@/utils/csv'
import { formatDateTime } from '@/utils/format'

const filters = reactive({ status: 'active' })

const { items, total, page, pageSize, loading, errorText, load, fetchAll } = usePagedList(
  listTeachers,
  10,
)

function query(): Record<string, unknown> {
  const params: Record<string, unknown> = {}
  if (filters.status) params.status = filters.status
  return params
}

function handleSearch(): void {
  page.value = 1
  void load(query())
}

function handleReset(): void {
  filters.status = 'active'
  handleSearch()
}

function handlePageChange(next: number): void {
  page.value = next
  void load(query())
}

function handleSizeChange(size: number): void {
  pageSize.value = size
  page.value = 1
  void load(query())
}

// ---------------------------------------------------------------- 表单

const dialogVisible = ref(false)
const submitting = ref(false)
const editingId = ref<string | null>(null)
const formRef = ref<FormInstance>()
const serverErrors = reactive<Record<string, string>>({})

const form = reactive({ name: '', phone: '', subject: '' })

const rules: FormRules<typeof form> = {
  name: [
    { required: true, message: '请输入老师姓名', trigger: 'blur' },
    { max: 64, message: '姓名最多 64 个字符', trigger: 'blur' },
  ],
  phone: [
    {
      validator: (_rule, value: string, callback) => {
        if (!value) {
          callback()
          return
        }
        if (!/^1\d{10}$/.test(value)) {
          callback(new Error('请输入 11 位手机号'))
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
  form.phone = ''
  form.subject = ''
  clearServerErrors()
  dialogVisible.value = true
}

function openEdit(row: Teacher): void {
  editingId.value = row.id
  form.name = row.name
  form.phone = row.phone ?? ''
  form.subject = row.subject ?? ''
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
    const payload: TeacherPayload = {
      name: form.name.trim(),
      phone: form.phone.trim() || null,
      subject: form.subject.trim() || null,
    }
    if (editingId.value) {
      await updateTeacher(editingId.value, payload)
      ElMessage.success('已保存')
    } else {
      await createTeacher(payload)
      ElMessage.success('已新建')
    }
    dialogVisible.value = false
    await load(query())
  } catch (error) {
    if (error instanceof ApiError && error.fieldErrors) {
      Object.assign(serverErrors, error.fieldErrors)
    } else {
      ElMessage.error(error instanceof ApiError ? error.message : '保存失败，请稍后重试')
    }
  } finally {
    submitting.value = false
  }
}

// ---------------------------------------------------------------- 停用 / 启用

async function changeStatus(row: Teacher, status: TeacherStatus): Promise<void> {
  const isDisable = status === 'inactive'
  try {
    await ElMessageBox.confirm(
      isDisable
        ? `停用「${row.name}」后排课与签到将受限（无法被排进新班级/课次）。确认停用？`
        : `确认恢复「${row.name}」为在职？`,
      isDisable ? '停用确认' : '启用确认',
      {
        type: 'warning',
        confirmButtonText: isDisable ? '确认停用' : '确认启用',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }

  try {
    await updateTeacher(row.id, { status })
    ElMessage.success(isDisable ? '已停用' : '已启用')
    await load(query())
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '操作失败')
  }
}

// ---------------------------------------------------------------- 导出

const exporting = ref(false)

async function handleExport(): Promise<void> {
  exporting.value = true
  try {
    const rows = await fetchAll(query())
    exportCsv<Teacher>(
      `老师档案-${new Date().toISOString().slice(0, 10)}.csv`,
      [
        { key: 'name', label: '姓名' },
        { key: 'phone', label: '手机号', format: (row) => row.phone ?? '' },
        { key: 'subject', label: '科目', format: (row) => row.subject ?? '' },
        { key: 'status', label: '状态', format: (row) => TEACHER_STATUS_LABELS[row.status] },
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
  void load(query())
})
</script>

<template>
  <div class="page">
    <el-card shadow="never" class="page__card">
      <div class="page__head">
        <div class="page__actions">
          <el-button plain :loading="exporting" @click="handleExport">导出 CSV</el-button>
          <el-button type="primary" @click="openCreate">新建老师</el-button>
        </div>
      </div>

      <el-form :inline="true" class="page__filters" @submit.prevent="handleSearch">
        <el-form-item label="状态">
          <el-select v-model="filters.status" placeholder="全部" clearable style="width: 120px">
            <el-option label="在职" value="active" />
            <el-option label="已停用" value="inactive" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" native-type="submit">查询</el-button>
          <el-button @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>

      <el-alert
        v-if="errorText"
        :title="errorText"
        type="error"
        show-icon
        :closable="false"
        class="page__error"
      />

      <el-table v-loading="loading" :data="items" border stripe empty-text="暂无老师">
        <el-table-column prop="name" label="姓名" min-width="120" />
        <el-table-column prop="phone" label="手机号" width="140">
          <template #default="{ row }">{{ row.phone || '-' }}</template>
        </el-table-column>
        <el-table-column prop="subject" label="科目" min-width="120">
          <template #default="{ row }">{{ row.subject || '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="row.status === 'inactive' ? 'info' : 'success'" size="small">
              {{ TEACHER_STATUS_LABELS[row.status as TeacherStatus] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button
              v-if="row.status === 'active'"
              link
              type="danger"
              @click="changeStatus(row, 'inactive')"
            >
              停用
            </el-button>
            <el-button v-else link type="primary" @click="changeStatus(row, 'active')">启用</el-button>
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

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑老师' : '新建老师'" width="460px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="80px">
        <el-form-item label="姓名" prop="name" :error="serverErrors.name">
          <el-input v-model="form.name" maxlength="64" placeholder="必填" @input="delete serverErrors.name" />
        </el-form-item>
        <el-form-item label="手机号" prop="phone" :error="serverErrors.phone">
          <el-input v-model="form.phone" maxlength="11" placeholder="11 位手机号，可不填" @input="delete serverErrors.phone" />
        </el-form-item>
        <el-form-item label="科目" prop="subject" :error="serverErrors.subject">
          <el-input v-model="form.subject" maxlength="64" placeholder="如 物理，可不填" @input="delete serverErrors.subject" />
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

.page__filters {
  margin-bottom: 4px;
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
