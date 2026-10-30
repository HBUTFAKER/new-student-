<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'

import { listClassrooms, listTeachers } from '@/api/archive'
import {
  createClass,
  enrollStudent,
  listActiveStudents,
  listClassStudents,
  listClasses,
  listCourses,
  removeStudent,
  updateClass,
} from '@/api/course'
import { ApiError } from '@/api/http'
import { usePagedList } from '@/composables/usePagedList'
import type { Classroom, Teacher } from '@/types/archive'
import { CLASS_STATUS_LABELS } from '@/types/course'
import type { ClassItem, ClassStatus, Course, Enrollment } from '@/types/course'
import { exportCsv } from '@/utils/csv'
import { formatDate, formatDateTime } from '@/utils/format'

const filters = reactive({ course_id: '', teacher_id: '', status: '' })

const { items, total, page, pageSize, loading, errorText, load, fetchAll } = usePagedList(
  listClasses,
  10,
)

function query(): Record<string, unknown> {
  const params: Record<string, unknown> = {}
  if (filters.course_id) params.course_id = filters.course_id
  if (filters.teacher_id) params.teacher_id = filters.teacher_id
  if (filters.status) params.status = filters.status
  return params
}

function handleSearch(): void {
  page.value = 1
  void load(query())
}

function handleReset(): void {
  filters.course_id = ''
  filters.teacher_id = ''
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

// ---------------------------------------------------------------- 下拉数据

const courses = ref<Course[]>([])
const teachers = ref<Teacher[]>([])
const classrooms = ref<Classroom[]>([])

async function loadOptions(): Promise<void> {
  try {
    const [courseData, teacherData, classroomData] = await Promise.all([
      listCourses({ page: 1, page_size: 100 }),
      listTeachers({ status: 'active', page: 1, page_size: 100 }),
      listClassrooms({ page: 1, page_size: 100 }),
    ])
    courses.value = courseData.items
    teachers.value = teacherData.items
    classrooms.value = classroomData.items
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '下拉数据加载失败')
  }
}

// ---------------------------------------------------------------- 班级表单

const dialogVisible = ref(false)
const submitting = ref(false)
const editingId = ref<string | null>(null)
const formRef = ref<FormInstance>()
const serverErrors = reactive<Record<string, string>>({})

const form = reactive({
  course_id: '',
  teacher_id: '',
  classroom_id: '',
  name: '',
  term_start: '',
  term_end: '',
  capacity: null as number | null,
})

const selectedClassroom = computed(() =>
  classrooms.value.find((item) => item.id === form.classroom_id),
)

const termError = ref('')

const rules: FormRules<typeof form> = {
  course_id: [{ required: true, message: '请选择课程', trigger: 'change' }],
  teacher_id: [{ required: true, message: '请选择老师', trigger: 'change' }],
  classroom_id: [{ required: true, message: '请选择教室', trigger: 'change' }],
  name: [
    { required: true, message: '请输入班级名称', trigger: 'blur' },
    { max: 128, message: '名称最多 128 个字符', trigger: 'blur' },
  ],
  capacity: [
    {
      validator: (_rule, value: number | null, callback) => {
        if (value === null || value === undefined) {
          callback()
          return
        }
        if (!Number.isInteger(value) || value <= 0) {
          callback(new Error('容量必须是正整数'))
          return
        }
        if (selectedClassroom.value && value > selectedClassroom.value.capacity) {
          callback(new Error(`不能超过教室容量 ${selectedClassroom.value.capacity}`))
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
  form.course_id = ''
  form.teacher_id = ''
  form.classroom_id = ''
  form.name = ''
  form.term_start = ''
  form.term_end = ''
  form.capacity = null
  termError.value = ''
  clearServerErrors()
  dialogVisible.value = true
}

function openEdit(row: ClassItem): void {
  editingId.value = row.id
  form.course_id = row.course_id
  form.teacher_id = row.teacher_id
  form.classroom_id = row.classroom_id
  form.name = row.name
  form.term_start = row.term_start ?? ''
  form.term_end = row.term_end ?? ''
  form.capacity = row.capacity
  termError.value = ''
  clearServerErrors()
  dialogVisible.value = true
}

function validateTerm(): boolean {
  termError.value = ''
  if (!form.term_start || !form.term_end) {
    termError.value = '请选择完整的学期起止日期'
    return false
  }
  if (form.term_end < form.term_start) {
    termError.value = '结束日期不能早于开始日期'
    return false
  }
  return true
}

async function submit(): Promise<void> {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  if (!validateTerm()) return

  clearServerErrors()
  submitting.value = true
  try {
    const base = {
      teacher_id: form.teacher_id,
      classroom_id: form.classroom_id,
      name: form.name.trim(),
      term_start: form.term_start,
      term_end: form.term_end,
      capacity: form.capacity,
    }
    if (editingId.value) {
      await updateClass(editingId.value, base)
      ElMessage.success('已保存')
    } else {
      await createClass({ ...base, course_id: form.course_id })
      ElMessage.success('已新建')
    }
    dialogVisible.value = false
    await load(query())
  } catch (error) {
    if (error instanceof ApiError && (error.code === 'CAPACITY_INVALID' || error.code === 'CAPACITY_EXCEEDED')) {
      serverErrors.capacity = error.message
      return
    }
    if (error instanceof ApiError && error.code === 'TERM_INVALID') {
      termError.value = error.message
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

// ---------------------------------------------------------------- 学员名单

const rosterVisible = ref(false)
const rosterLoading = ref(false)
const currentClass = ref<ClassItem | null>(null)
const roster = ref<Enrollment[]>([])

const capacityText = computed(() => {
  const klass = currentClass.value
  if (!klass) return ''
  return `${klass.enrolled} / ${klass.capacity ?? klass.classroom_capacity}`
})

async function openRoster(row: ClassItem): Promise<void> {
  currentClass.value = row
  rosterVisible.value = true
  await loadRoster(row.id)
}

async function loadRoster(classId: string): Promise<void> {
  rosterLoading.value = true
  try {
    const data = await listClassStudents(classId)
    roster.value = data.items
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '名单加载失败')
  } finally {
    rosterLoading.value = false
  }
}

const enrollVisible = ref(false)
const enrolling = ref(false)
const enrollStudentId = ref('')
const candidates = ref<{ id: string; name: string; grade: string | null }[]>([])

const availableCandidates = computed(() => {
  const enrolled = new Set(roster.value.map((item) => item.student_id))
  return candidates.value.filter((student) => !enrolled.has(student.id))
})

async function openEnroll(): Promise<void> {
  enrollStudentId.value = ''
  enrollVisible.value = true
  try {
    const data = await listActiveStudents()
    candidates.value = data.items.map((student) => ({
      id: student.id,
      name: student.name,
      grade: student.grade,
    }))
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '学生列表加载失败')
  }
}

async function submitEnroll(): Promise<void> {
  if (!currentClass.value) return
  if (!enrollStudentId.value) {
    ElMessage.warning('请选择学生')
    return
  }
  const classId = currentClass.value.id
  enrolling.value = true
  try {
    await enrollStudent(classId, enrollStudentId.value)
    ElMessage.success('已加入班级')
    enrollVisible.value = false
    await loadRoster(classId)
    await load(query())
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '加入失败')
  } finally {
    enrolling.value = false
  }
}

async function handleRemove(student: Enrollment): Promise<void> {
  if (!currentClass.value) return
  try {
    await removeStudent(currentClass.value.id, student.student_id)
    ElMessage.success('已移出班级')
    await loadRoster(currentClass.value.id)
    await load(query())
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '移出失败')
  }
}

// ---------------------------------------------------------------- 导出

const exporting = ref(false)

async function handleExport(): Promise<void> {
  exporting.value = true
  try {
    const rows = await fetchAll(query())
    exportCsv<ClassItem>(
      `班级列表-${new Date().toISOString().slice(0, 10)}.csv`,
      [
        { key: 'name', label: '班级名称' },
        { key: 'course_name', label: '课程' },
        { key: 'teacher_name', label: '老师' },
        { key: 'classroom_name', label: '教室' },
        { key: 'enrolled', label: '已报', format: (row) => String(row.enrolled) },
        {
          key: 'capacity',
          label: '容量',
          format: (row) => String(row.capacity ?? row.classroom_capacity),
        },
        {
          key: 'term_start',
          label: '学期',
          format: (row) => `${formatDate(row.term_start)} ~ ${formatDate(row.term_end)}`,
        },
        { key: 'status', label: '状态', format: (row) => CLASS_STATUS_LABELS[row.status] },
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

onMounted(async () => {
  await loadOptions()
  await load(query())
})
</script>

<template>
  <div class="page">
    <el-card shadow="never" class="page__card">
      <div class="page__head">
        <div class="page__actions">
          <el-button plain :loading="exporting" @click="handleExport">导出 CSV</el-button>
          <el-button type="primary" @click="openCreate">新建班级</el-button>
        </div>
      </div>

      <el-form :inline="true" class="page__filters" @submit.prevent="handleSearch">
        <el-form-item label="课程">
          <el-select v-model="filters.course_id" placeholder="全部" clearable filterable style="width: 160px">
            <el-option v-for="course in courses" :key="course.id" :label="course.name" :value="course.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="老师">
          <el-select v-model="filters.teacher_id" placeholder="全部" clearable filterable style="width: 140px">
            <el-option v-for="teacher in teachers" :key="teacher.id" :label="teacher.name" :value="teacher.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="filters.status" placeholder="全部" clearable style="width: 120px">
            <el-option label="进行中" value="active" />
            <el-option label="已结课" value="ended" />
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

      <el-table v-loading="loading" :data="items" border stripe empty-text="暂无班级">
        <el-table-column prop="name" label="班级名称" min-width="130" />
        <el-table-column prop="course_name" label="课程" min-width="130" />
        <el-table-column prop="teacher_name" label="老师" min-width="120" />
        <el-table-column prop="classroom_name" label="教室" min-width="120" />
        <el-table-column label="人数" width="90">
          <template #default="{ row }">
            {{ row.enrolled }} / {{ row.capacity ?? row.classroom_capacity }}
          </template>
        </el-table-column>
        <el-table-column label="学期" width="200">
          <template #default="{ row }">{{ formatDate(row.term_start) }} ~ {{ formatDate(row.term_end) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'ended' ? 'info' : 'success'" size="small">
              {{ CLASS_STATUS_LABELS[row.status as ClassStatus] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="primary" @click="openRoster(row)">学员名单</el-button>
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

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑班级' : '新建班级'" width="540px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="班级名称" prop="name" :error="serverErrors.name">
          <el-input v-model="form.name" maxlength="128" placeholder="如 冲刺A班" @input="delete serverErrors.name" />
        </el-form-item>
        <el-form-item label="课程" prop="course_id" :error="serverErrors.course_id">
          <el-select
            v-model="form.course_id"
            placeholder="选择课程"
            filterable
            :disabled="editingId !== null"
            style="width: 100%"
          >
            <el-option v-for="course in courses" :key="course.id" :label="course.name" :value="course.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="老师" prop="teacher_id" :error="serverErrors.teacher_id">
          <el-select v-model="form.teacher_id" placeholder="选择老师" filterable style="width: 100%">
            <el-option v-for="teacher in teachers" :key="teacher.id" :label="teacher.name" :value="teacher.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="教室" prop="classroom_id" :error="serverErrors.classroom_id">
          <el-select v-model="form.classroom_id" placeholder="选择教室" filterable style="width: 100%">
            <el-option
              v-for="room in classrooms"
              :key="room.id"
              :label="`${room.name}（容量 ${room.capacity}）`"
              :value="room.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="学期" required>
          <div class="form__term-wrap">
            <div class="form__term">
              <el-date-picker
                v-model="form.term_start"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="开始日期"
                style="width: 100%"
                @change="termError = ''"
              />
              <span class="form__term-sep">至</span>
              <el-date-picker
                v-model="form.term_end"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="结束日期"
                style="width: 100%"
                @change="termError = ''"
              />
            </div>
            <div v-if="termError" class="form__error">{{ termError }}</div>
          </div>
        </el-form-item>
        <el-form-item label="容量" prop="capacity" :error="serverErrors.capacity">
          <el-input-number
            v-model="form.capacity"
            :min="1"
            :max="selectedClassroom?.capacity ?? 10000"
            :step="1"
            placeholder="留空 = 教室容量"
            style="width: 100%"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="rosterVisible" :title="`${currentClass?.name ?? ''} · 学员名单`" size="560px">
      <div class="roster">
        <div class="roster__head">
          <span class="roster__count">已报 {{ capacityText }}</span>
          <el-button type="primary" size="small" @click="openEnroll">添加学员</el-button>
        </div>

        <el-table v-loading="rosterLoading" :data="roster" border empty-text="还没有学员">
          <el-table-column prop="student_name" label="姓名" min-width="120" />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="row.student_status === 'active' ? 'success' : 'info'" size="small">
                {{ row.student_status === 'active' ? '在读' : '非在读' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="入班日期" width="120">
            <template #default="{ row }">{{ formatDate(row.joined_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="90">
            <template #default="{ row }">
              <el-button link type="danger" @click="handleRemove(row)">移出</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-drawer>

    <el-dialog v-model="enrollVisible" title="添加学员" width="420px" append-to-body>
      <el-form label-width="70px">
        <el-form-item label="学生" required>
          <el-select v-model="enrollStudentId" filterable placeholder="选择在读学生" style="width: 100%">
            <el-option
              v-for="student in availableCandidates"
              :key="student.id"
              :label="`${student.name}${student.grade ? ' · ' + student.grade : ''}`"
              :value="student.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="enrollVisible = false">取消</el-button>
        <el-button type="primary" :loading="enrolling" @click="submitEnroll">确认加入</el-button>
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

.form__term-wrap {
  width: 100%;
}

.form__term {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.form__term-sep {
  color: #909399;
  font-size: 13px;
}

.form__error {
  margin-top: 4px;
  font-size: 12px;
  color: #f56c6c;
}

.roster {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.roster__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.roster__count {
  font-size: 13px;
  color: #606266;
}
</style>
