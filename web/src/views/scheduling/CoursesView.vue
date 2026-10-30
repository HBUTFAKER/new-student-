<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'

import { createCourse, deleteCourse, listCourses, updateCourse } from '@/api/course'
import { ApiError } from '@/api/http'
import { usePagedList } from '@/composables/usePagedList'
import { COURSE_STATUS_LABELS } from '@/types/course'
import type { Course, CoursePayload, CourseStatus } from '@/types/course'
import { formatDateTime } from '@/utils/format'

const filters = reactive({ keyword: '', status: '' })

const { items, total, page, pageSize, loading, errorText, load } = usePagedList(listCourses, 10)

function query(): Record<string, unknown> {
  const params: Record<string, unknown> = {}
  if (filters.keyword.trim()) params.keyword = filters.keyword.trim()
  if (filters.status) params.status = filters.status
  return params
}

function handleSearch(): void {
  page.value = 1
  void load(query())
}

function handleReset(): void {
  filters.keyword = ''
  filters.status = ''
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

const form = reactive({
  name: '',
  subject: '',
  duration_min: null as number | null,
  remark: '',
  status: 'active' as CourseStatus,
})

const rules: FormRules<typeof form> = {
  name: [
    { required: true, message: '请输入课程名称', trigger: 'blur' },
    { max: 128, message: '名称最多 128 个字符', trigger: 'blur' },
  ],
  subject: [{ max: 64, message: '科目最多 64 个字符', trigger: 'blur' }],
  duration_min: [
    {
      validator: (_rule, value: number | null, callback) => {
        if (value === null || value === undefined) {
          callback()
          return
        }
        if (!Number.isInteger(value) || value < 5 || value > 600) {
          callback(new Error('时长必须是 5–600 的整数分钟'))
          return
        }
        callback()
      },
      trigger: 'blur',
    },
  ],
  remark: [{ max: 255, message: '备注最多 255 个字符', trigger: 'blur' }],
}

function clearServerErrors(): void {
  Object.keys(serverErrors).forEach((key) => delete serverErrors[key])
}

function openCreate(): void {
  editingId.value = null
  form.name = ''
  form.subject = ''
  form.duration_min = null
  form.remark = ''
  form.status = 'active'
  clearServerErrors()
  dialogVisible.value = true
}

function openEdit(row: Course): void {
  editingId.value = row.id
  form.name = row.name
  form.subject = row.subject ?? ''
  form.duration_min = row.duration_min
  form.remark = row.remark ?? ''
  form.status = row.status
  clearServerErrors()
  dialogVisible.value = true
}

function payload(): CoursePayload {
  return {
    name: form.name.trim(),
    subject: form.subject.trim() || null,
    duration_min: form.duration_min ?? null,
    remark: form.remark.trim() || null,
    status: form.status,
  }
}

async function submit(): Promise<void> {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  clearServerErrors()
  submitting.value = true
  try {
    if (editingId.value) {
      await updateCourse(editingId.value, payload())
      ElMessage.success('已保存')
    } else {
      await createCourse(payload())
      ElMessage.success('已新建')
    }
    dialogVisible.value = false
    await load(query())
  } catch (error) {
    if (error instanceof ApiError && error.fieldErrors) {
      Object.assign(serverErrors, error.fieldErrors)
      return
    }
    if (error instanceof ApiError && error.status === 422) {
      serverErrors.name = error.message
      return
    }
    ElMessage.error(error instanceof ApiError ? error.message : '保存失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}

async function toggleStatus(row: Course): Promise<void> {
  const next: CourseStatus = row.status === 'active' ? 'inactive' : 'active'
  const verb = next === 'inactive' ? '停用' : '启用'
  try {
    await ElMessageBox.confirm(
      next === 'inactive'
        ? `停用「${row.name}」后不能再为它建新班，已存在的班级不受影响。确认停用？`
        : `启用「${row.name}」后可以继续为它建班。确认启用？`,
      `${verb}课程`,
      {
        confirmButtonText: `确认${verb}`,
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }
  try {
    await updateCourse(row.id, { status: next })
    ElMessage.success(`已${verb}`)
    await load(query())
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : `${verb}失败`)
  }
}

async function remove(row: Course): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `删除后不可恢复。若该课程下有班级，后端会拒绝删除——只能改为停用。确认删除「${row.name}」？`,
      '删除课程',
      { confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await deleteCourse(row.id)
    ElMessage.success('已删除')
    await load(query())
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '删除失败')
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
        <div class="page__filters">
          <el-input
            v-model="filters.keyword"
            placeholder="课程名称"
            clearable
            style="width: 200px"
            @keyup.enter="handleSearch"
            @clear="handleSearch"
          />
          <el-select v-model="filters.status" placeholder="状态" clearable style="width: 120px">
            <el-option label="启用" value="active" />
            <el-option label="停用" value="inactive" />
          </el-select>
          <el-button type="primary" plain @click="handleSearch">查询</el-button>
          <el-button @click="handleReset">重置</el-button>
        </div>
        <el-button type="primary" @click="openCreate">新建课程</el-button>
      </div>

      <el-alert
        v-if="errorText"
        :title="errorText"
        type="error"
        show-icon
        :closable="false"
        class="page__error"
      />

      <el-table v-loading="loading" :data="items" border stripe empty-text="暂无课程">
        <el-table-column prop="name" label="课程名称" min-width="180" />
        <el-table-column prop="subject" label="科目" width="120">
          <template #default="{ row }">{{ row.subject || '-' }}</template>
        </el-table-column>
        <el-table-column label="时长" width="100">
          <template #default="{ row }">
            {{ row.duration_min ? `${row.duration_min} 分钟` : '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="remark" label="备注" min-width="180">
          <template #default="{ row }">{{ row.remark || '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : 'info'" effect="plain" size="small">
              {{ COURSE_STATUS_LABELS[row.status as CourseStatus] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="primary" @click="toggleStatus(row)">
              {{ row.status === 'active' ? '停用' : '启用' }}
            </el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
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

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑课程' : '新建课程'" width="520px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="课程名称" prop="name" :error="serverErrors.name">
          <el-input
            v-model="form.name"
            maxlength="128"
            placeholder="如 初三物理冲刺"
            @input="delete serverErrors.name"
          />
        </el-form-item>
        <el-form-item label="科目" prop="subject" :error="serverErrors.subject">
          <el-input v-model="form.subject" maxlength="64" placeholder="如 物理（可不填）" />
        </el-form-item>
        <el-form-item label="单次时长" prop="duration_min" :error="serverErrors.duration_min">
          <el-input-number
            v-model="form.duration_min"
            :min="5"
            :max="600"
            :step="5"
            placeholder="分钟"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="备注" prop="remark" :error="serverErrors.remark">
          <el-input
            v-model="form.remark"
            type="textarea"
            :rows="2"
            maxlength="255"
            show-word-limit
            placeholder="可不填"
          />
        </el-form-item>
        <el-form-item label="状态" prop="status">
          <el-radio-group v-model="form.status">
            <el-radio value="active">启用</el-radio>
            <el-radio value="inactive">停用</el-radio>
          </el-radio-group>
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
  justify-content: space-between;
  margin-bottom: 16px;
}

.page__filters {
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
