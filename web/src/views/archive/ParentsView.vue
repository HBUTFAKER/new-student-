<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'

import {
  createBinding,
  createParent,
  deleteBinding,
  listBindings,
  listParents,
  listStudents,
  updateParent,
} from '@/api/archive'
import { ApiError } from '@/api/http'
import ImportDialog from '@/components/ImportDialog.vue'
import { usePagedList } from '@/composables/usePagedList'
import type { Binding, Parent, Student } from '@/types/archive'
import { exportCsv } from '@/utils/csv'
import { formatDateTime } from '@/utils/format'

const filters = reactive({ phone: '' })

const { items, total, page, pageSize, loading, errorText, load, fetchAll } = usePagedList(
  listParents,
  10,
)

function query(): Record<string, unknown> {
  const params: Record<string, unknown> = {}
  if (filters.phone.trim()) params.phone = filters.phone.trim()
  return params
}

function handleSearch(): void {
  page.value = 1
  void load(query())
}

function handleReset(): void {
  filters.phone = ''
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

// ---------------------------------------------------------------- 绑定关系（前端聚合展示）

const bindings = ref<Binding[]>([])
const students = ref<Student[]>([])

const studentNameMap = computed(() => {
  const map = new Map<string, string>()
  for (const student of students.value) map.set(student.id, student.name)
  return map
})

const bindingsByParent = computed(() => {
  const map = new Map<string, Binding[]>()
  for (const binding of bindings.value) {
    const list = map.get(binding.parent_id) ?? []
    list.push(binding)
    map.set(binding.parent_id, list)
  }
  return map
})

function boundOf(parentId: string): Binding[] {
  return bindingsByParent.value.get(parentId) ?? []
}

async function loadRelations(): Promise<void> {
  try {
    const [bindingData, studentData] = await Promise.all([
      listBindings({ page: 1, page_size: 100 }),
      listStudents({ page: 1, page_size: 100 }),
    ])
    bindings.value = bindingData.items
    students.value = studentData.items
  } catch {
    // 绑定关系是辅助信息，失败不打断主列表
    bindings.value = []
  }
}

// ---------------------------------------------------------------- 家长表单

const dialogVisible = ref(false)
const submitting = ref(false)
const editingId = ref<string | null>(null)
const formRef = ref<FormInstance>()
const serverErrors = reactive<Record<string, string>>({})

const form = reactive({ name: '', phone: '' })

const rules: FormRules<typeof form> = {
  name: [
    { required: true, message: '请输入家长姓名', trigger: 'blur' },
    { max: 64, message: '姓名最多 64 个字符', trigger: 'blur' },
  ],
  phone: [
    { required: true, message: '请输入手机号', trigger: 'blur' },
    { pattern: /^1\d{10}$/, message: '请输入 11 位手机号', trigger: 'blur' },
  ],
}

function clearServerErrors(): void {
  for (const key of Object.keys(serverErrors)) delete serverErrors[key]
}

function openCreate(): void {
  editingId.value = null
  form.name = ''
  form.phone = ''
  clearServerErrors()
  dialogVisible.value = true
}

function openEdit(row: Parent): void {
  editingId.value = row.id
  form.name = row.name
  form.phone = row.phone
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
    if (editingId.value) {
      await updateParent(editingId.value, { name: form.name.trim(), phone: form.phone.trim() })
      ElMessage.success('已保存')
    } else {
      await createParent({ name: form.name.trim(), phone: form.phone.trim() })
      ElMessage.success('已新建')
    }
    dialogVisible.value = false
    await load(query())
    await loadRelations()
  } catch (error) {
    if (error instanceof ApiError && error.code === 'PARENT_DUPLICATED') {
      serverErrors.phone = '该手机号已存在'
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

// ---------------------------------------------------------------- 绑定 / 解绑

const bindVisible = ref(false)
const bindSubmitting = ref(false)
const bindTarget = ref<Parent | null>(null)
const bindForm = reactive({ student_id: '', relation: '父' })

const bindableStudents = computed(() => {
  const bound = new Set(boundOf(bindTarget.value?.id ?? '').map((item) => item.student_id))
  return students.value.filter((student) => !bound.has(student.id) && student.status !== 'archived')
})

function openBind(row: Parent): void {
  bindTarget.value = row
  bindForm.student_id = ''
  bindForm.relation = '父'
  bindVisible.value = true
}

async function submitBind(): Promise<void> {
  if (!bindTarget.value) return
  if (!bindForm.student_id) {
    ElMessage.warning('请选择学生')
    return
  }
  bindSubmitting.value = true
  try {
    await createBinding({
      parent_id: bindTarget.value.id,
      student_id: bindForm.student_id,
      relation: bindForm.relation || null,
    })
    ElMessage.success('已绑定')
    bindVisible.value = false
    await loadRelations()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '绑定失败')
  } finally {
    bindSubmitting.value = false
  }
}

async function handleUnbind(row: Parent, binding: Binding): Promise<void> {
  const studentName = studentNameMap.value.get(binding.student_id) ?? '该学生'
  try {
    await ElMessageBox.confirm(`确认解除「${row.name}」与「${studentName}」的绑定？`, '解绑确认', {
      type: 'warning',
      confirmButtonText: '确认解绑',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await deleteBinding(binding.id)
    ElMessage.success('已解绑')
    await loadRelations()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '解绑失败')
  }
}

// ---------------------------------------------------------------- 导出 / 导入

const exporting = ref(false)

async function handleExport(): Promise<void> {
  exporting.value = true
  try {
    const rows = await fetchAll(query())
    exportCsv<Parent>(
      `家长档案-${new Date().toISOString().slice(0, 10)}.csv`,
      [
        { key: 'name', label: '姓名' },
        { key: 'phone', label: '手机号' },
        {
          key: 'id',
          label: '已绑定学生',
          format: (row) =>
            boundOf(row.id)
              .map((binding) => studentNameMap.value.get(binding.student_id) ?? binding.student_id)
              .join('、'),
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

const importVisible = ref(false)

async function submitImportRow(row: Record<string, string>): Promise<void> {
  const name = (row['姓名'] ?? '').trim()
  const phone = (row['手机号'] ?? '').trim()
  if (!name) throw new ApiError('姓名为空', 'ROW_INVALID', 422)
  if (!/^1\d{10}$/.test(phone)) throw new ApiError('手机号格式不正确', 'ROW_INVALID', 422)
  await createParent({ name, phone })
}

async function handleImported(): Promise<void> {
  await load(query())
  await loadRelations()
}

onMounted(async () => {
  await load(query())
  await loadRelations()
})
</script>

<template>
  <div class="page">
    <el-card shadow="never" class="page__card">
      <div class="page__head">
        <div class="page__actions">
          <el-button plain :loading="exporting" @click="handleExport">导出 CSV</el-button>
          <el-button plain @click="importVisible = true">批量导入</el-button>
          <el-button type="primary" @click="openCreate">新建家长</el-button>
        </div>
      </div>

      <el-form :inline="true" class="page__filters" @submit.prevent="handleSearch">
        <el-form-item label="手机号">
          <el-input v-model="filters.phone" placeholder="精确匹配" clearable style="width: 160px" />
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

      <el-table v-loading="loading" :data="items" border stripe empty-text="暂无家长">
        <el-table-column prop="name" label="姓名" min-width="110" />
        <el-table-column prop="phone" label="手机号" width="140" />
        <el-table-column label="已绑定学生" min-width="220">
          <template #default="{ row }">
            <template v-if="boundOf(row.id).length > 0">
              <el-tag
                v-for="binding in boundOf(row.id)"
                :key="binding.id"
                closable
                size="small"
                class="page__student-tag"
                @close="handleUnbind(row, binding)"
              >
                {{ studentNameMap.get(binding.student_id) ?? '未知学生' }}
                <span v-if="binding.relation" class="page__relation">·{{ binding.relation }}</span>
              </el-tag>
            </template>
            <span v-else class="page__muted">未绑定</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="primary" @click="openBind(row)">绑定学生</el-button>
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

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑家长' : '新建家长'" width="460px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="80px">
        <el-form-item label="姓名" prop="name" :error="serverErrors.name">
          <el-input v-model="form.name" maxlength="64" placeholder="必填" @input="delete serverErrors.name" />
        </el-form-item>
        <el-form-item label="手机号" prop="phone" :error="serverErrors.phone">
          <el-input
            v-model="form.phone"
            maxlength="11"
            placeholder="11 位手机号，租户内唯一"
            @input="delete serverErrors.phone"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="bindVisible" title="绑定学生" width="460px">
      <el-form label-width="80px">
        <el-form-item label="家长">
          <span>{{ bindTarget?.name }}（{{ bindTarget?.phone }}）</span>
        </el-form-item>
        <el-form-item label="学生" required>
          <el-select v-model="bindForm.student_id" filterable placeholder="选择在读学生" style="width: 100%">
            <el-option
              v-for="student in bindableStudents"
              :key="student.id"
              :label="`${student.name}${student.grade ? ' · ' + student.grade : ''}`"
              :value="student.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="关系">
          <el-select v-model="bindForm.relation" style="width: 100%">
            <el-option label="父" value="父" />
            <el-option label="母" value="母" />
            <el-option label="其他" value="其他" />
          </el-select>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="bindVisible = false">取消</el-button>
        <el-button type="primary" :loading="bindSubmitting" @click="submitBind">确认绑定</el-button>
      </template>
    </el-dialog>

    <ImportDialog
      v-model="importVisible"
      title="批量导入家长"
      template-file-name="家长导入模板.csv"
      :template-headers="['姓名', '手机号']"
      :template-sample="['王大力', '13900000000']"
      :submit-row="submitImportRow"
      @finished="handleImported"
    />
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

.page__student-tag {
  margin: 2px 6px 2px 0;
}

.page__relation {
  color: #909399;
}

.page__muted {
  color: #c0c4cc;
  font-size: 13px;
}
</style>
