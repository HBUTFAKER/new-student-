<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'

import { createStudent, listStudents, updateStudent } from '@/api/archive'
import { ApiError } from '@/api/http'
import ImportDialog from '@/components/ImportDialog.vue'
import InviteCodeDialog from '@/components/InviteCodeDialog.vue'
import { usePagedList } from '@/composables/usePagedList'
import {
  GRADE_OPTIONS,
  STUDENT_STATUS_LABELS,
  STUDENT_STATUS_OPTIONS,
} from '@/types/archive'
import type { Student, StudentPayload, StudentStatus } from '@/types/archive'
import { exportCsv } from '@/utils/csv'
import { formatDateTime } from '@/utils/format'

const filters = reactive({
  name: '',
  grade: '',
  status: 'active',
})

const { items, total, page, pageSize, loading, errorText, load, fetchAll } = usePagedList(
  listStudents,
  10,
)

function query(): Record<string, unknown> {
  const params: Record<string, unknown> = {}
  if (filters.name.trim()) params.name = filters.name.trim()
  if (filters.grade) params.grade = filters.grade
  if (filters.status) params.status = filters.status
  return params
}

function handleSearch(): void {
  page.value = 1
  void load(query())
}

function handleReset(): void {
  filters.name = ''
  filters.grade = ''
  filters.status = 'active'
  handleSearch()
}

function handleSizeChange(size: number): void {
  pageSize.value = size
  page.value = 1
  void load(query())
}

function handlePageChange(next: number): void {
  page.value = next
  void load(query())
}

// ---------------------------------------------------------------- 表单

const dialogVisible = ref(false)
const submitting = ref(false)
const editingId = ref<string | null>(null)
const formRef = ref<FormInstance>()
const serverErrors = reactive<Record<string, string>>({})

const form = reactive({
  name: '',
  grade: '',
  school: '',
  contact: '',
  status: 'active' as StudentStatus,
})

const rules: FormRules<typeof form> = {
  name: [
    { required: true, message: '请输入学生姓名', trigger: 'blur' },
    { max: 64, message: '姓名最多 64 个字符', trigger: 'blur' },
  ],
  contact: [
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
  form.grade = ''
  form.school = ''
  form.contact = ''
  form.status = 'active'
  clearServerErrors()
  dialogVisible.value = true
}

function openEdit(row: Student): void {
  editingId.value = row.id
  form.name = row.name
  form.grade = row.grade ?? ''
  form.school = row.school ?? ''
  form.contact = row.contact ?? ''
  form.status = row.status
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
    const payload: StudentPayload = {
      name: form.name.trim(),
      grade: form.grade || null,
      school: form.school.trim() || null,
      contact: form.contact.trim() || null,
    }
    if (editingId.value) {
      await updateStudent(editingId.value, { ...payload, status: form.status })
      ElMessage.success('已保存')
    } else {
      await createStudent(payload)
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

// ---------------------------------------------------------------- 归档 / 恢复

async function changeStatus(row: Student, status: StudentStatus): Promise<void> {
  const isArchive = status === 'archived'
  try {
    await ElMessageBox.confirm(
      isArchive
        ? `归档后「${row.name}」不再显示在列表中，历史数据保留。确认归档？`
        : `确认恢复「${row.name}」为在读状态？`,
      isArchive ? '归档确认' : '恢复确认',
      {
        type: 'warning',
        confirmButtonText: isArchive ? '确认归档' : '确认恢复',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }

  try {
    await updateStudent(row.id, { status })
    ElMessage.success(isArchive ? '已归档' : '已恢复')
    await load(query())
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '操作失败')
  }
}

// ---------------------------------------------------------------- 导出 / 导入

const exporting = ref(false)

async function handleExport(): Promise<void> {
  exporting.value = true
  try {
    const rows = await fetchAll(query())
    exportCsv<Student>(
      `学生档案-${new Date().toISOString().slice(0, 10)}.csv`,
      [
        { key: 'name', label: '姓名' },
        { key: 'grade', label: '年级', format: (row) => row.grade ?? '' },
        { key: 'school', label: '学校', format: (row) => row.school ?? '' },
        { key: 'contact', label: '联系方式', format: (row) => row.contact ?? '' },
        { key: 'status', label: '状态', format: (row) => STUDENT_STATUS_LABELS[row.status] },
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

const importVisible = ref(false)

// ---------------------------------------------------------------- 邀请码

const inviteVisible = ref(false)
const inviteStudent = ref<Student | null>(null)

function openInvite(row: Student): void {
  inviteStudent.value = row
  inviteVisible.value = true
}

async function submitImportRow(row: Record<string, string>): Promise<void> {
  const name = (row['姓名'] ?? '').trim()
  if (!name) throw new ApiError('姓名为空', 'ROW_INVALID', 422)
  await createStudent({
    name,
    grade: row['年级']?.trim() || null,
    school: row['学校']?.trim() || null,
    contact: row['联系方式']?.trim() || null,
  })
}

function handleImported(): void {
  void load(query())
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
          <el-button plain @click="importVisible = true">批量导入</el-button>
          <el-button type="primary" @click="openCreate">新建学生</el-button>
        </div>
      </div>

      <el-form :inline="true" class="page__filters" @submit.prevent="handleSearch">
        <el-form-item label="姓名">
          <el-input v-model="filters.name" placeholder="模糊匹配" clearable style="width: 140px" />
        </el-form-item>
        <el-form-item label="年级">
          <el-select v-model="filters.grade" placeholder="全部" clearable style="width: 120px">
            <el-option v-for="grade in GRADE_OPTIONS" :key="grade" :label="grade" :value="grade" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="filters.status" placeholder="全部" clearable style="width: 120px">
            <el-option
              v-for="option in STUDENT_STATUS_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
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

      <el-table v-loading="loading" :data="items" border stripe empty-text="暂无学生">
        <el-table-column prop="name" label="姓名" min-width="110" />
        <el-table-column prop="grade" label="年级" width="100">
          <template #default="{ row }">{{ row.grade || '-' }}</template>
        </el-table-column>
        <el-table-column prop="school" label="学校" min-width="120">
          <template #default="{ row }">{{ row.school || '-' }}</template>
        </el-table-column>
        <el-table-column prop="contact" label="联系方式" width="130">
          <template #default="{ row }">{{ row.contact || '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'archived' ? 'info' : row.status === 'suspended' ? 'warning' : 'success'" size="small">
              {{ STUDENT_STATUS_LABELS[row.status as StudentStatus] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="primary" @click="openInvite(row)">邀请码</el-button>
            <el-button
              v-if="row.status !== 'archived'"
              link
              type="danger"
              @click="changeStatus(row, 'archived')"
            >
              归档
            </el-button>
            <el-button v-else link type="primary" @click="changeStatus(row, 'active')">恢复</el-button>
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

    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? '编辑学生' : '新建学生'"
      width="480px"
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="80px">
        <el-form-item label="姓名" prop="name" :error="serverErrors.name">
          <el-input v-model="form.name" maxlength="64" placeholder="必填" @input="delete serverErrors.name" />
        </el-form-item>
        <el-form-item label="年级" prop="grade" :error="serverErrors.grade">
          <el-select v-model="form.grade" placeholder="选择年级" clearable filterable allow-create style="width: 100%">
            <el-option v-for="grade in GRADE_OPTIONS" :key="grade" :label="grade" :value="grade" />
          </el-select>
        </el-form-item>
        <el-form-item label="学校" prop="school" :error="serverErrors.school">
          <el-input v-model="form.school" maxlength="128" placeholder="学校名称（可不填）" @input="delete serverErrors.school" />
        </el-form-item>
        <el-form-item label="联系方式" prop="contact" :error="serverErrors.contact">
          <el-input v-model="form.contact" maxlength="11" placeholder="11 位手机号，可不填" @input="delete serverErrors.contact" />
        </el-form-item>
        <el-form-item v-if="editingId" label="状态" prop="status">
          <el-select v-model="form.status" style="width: 100%">
            <el-option
              v-for="option in STUDENT_STATUS_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <ImportDialog
      v-model="importVisible"
      title="批量导入学生"
      template-file-name="学生导入模板.csv"
      :template-headers="['姓名', '年级', '学校', '联系方式']"
      :template-sample="['王小明', '八年级', '三中', '13800000000']"
      :submit-row="submitImportRow"
      @finished="handleImported"
    />

    <InviteCodeDialog v-model="inviteVisible" :student="inviteStudent" />
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
