<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'

import { createTeacher, listTeachers } from '@/api/archive'
import { createStaff, listStaff, updateStaff } from '@/api/staff'
import { ApiError } from '@/api/http'
import { usePagedList } from '@/composables/usePagedList'
import { ROLE_LABELS, STAFF_ROLES } from '@/types/auth'
import type { StaffRole } from '@/types/auth'
import type { Teacher } from '@/types/archive'
import { STAFF_STATUS_LABELS } from '@/types/staff'
import type { Staff, StaffPayload, StaffStatus } from '@/types/staff'
import { formatDateTime } from '@/utils/format'

const { items, total, page, pageSize, loading, errorText, load } = usePagedList(listStaff, 10)

// ---------------------------------------------------------------- 老师档案映射

const teachers = ref<Teacher[]>([])
const teacherNameMap = computed<Record<string, string>>(() =>
  Object.fromEntries(teachers.value.map((t) => [t.id, t.name])),
)
const activeTeachers = computed(() => teachers.value.filter((t) => t.status === 'active'))

async function fetchAllTeachers(): Promise<void> {
  const all: Teacher[] = []
  let current = 1
  for (;;) {
    const data = await listTeachers({ page: current, page_size: 100 })
    all.push(...data.items)
    if (data.items.length === 0 || all.length >= data.total) break
    current += 1
  }
  teachers.value = all
}

function teacherName(id: string | null): string {
  if (!id) return '-'
  return teacherNameMap.value[id] ?? '-'
}

function reload(): void {
  void load({ page: page.value, page_size: pageSize.value })
  void fetchAllTeachers()
}

function handlePageChange(next: number): void {
  page.value = next
  reload()
}

function handleSizeChange(size: number): void {
  pageSize.value = size
  page.value = 1
  reload()
}

// ---------------------------------------------------------------- 友好文案

function staffErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'CANNOT_DISABLE_SELF') return '不能停用自己'
    if (error.code === 'LAST_ADMIN') return '不能停用或降级本机构最后一个管理员'
    return error.message
  }
  return '操作失败，请稍后重试'
}

// ---------------------------------------------------------------- 新建 / 编辑账号

const dialogVisible = ref(false)
const submitting = ref(false)
const formRef = ref<FormInstance>()
const serverErrors = reactive<Record<string, string>>({})

const form = reactive({
  name: '',
  phone: '',
  role: 'teacher' as StaffRole,
  password: '',
  teacher_id: '' as string,
})

const rules: FormRules<typeof form> = {
  name: [{ required: true, message: '请输入姓名', trigger: 'blur' }],
  phone: [
    { required: true, message: '请输入手机号', trigger: 'blur' },
    {
      validator: (_rule, value: string, callback) => {
        if (!/^1\d{10}$/.test(value)) callback(new Error('请输入 11 位手机号'))
        else callback()
      },
      trigger: 'blur',
    },
  ],
  role: [{ required: true, message: '请选择角色', trigger: 'change' }],
  password: [
    { required: true, message: '请输入初始密码', trigger: 'blur' },
    { min: 8, max: 72, message: '密码 8~72 个字符', trigger: 'blur' },
  ],
  teacher_id: [
    {
      validator: (_rule, value: string, callback) => {
        if (form.role === 'teacher' && !value) callback(new Error('老师角色必须关联老师档案'))
        else callback()
      },
      trigger: 'change',
    },
  ],
}

function clearServerErrors(): void {
  for (const key of Object.keys(serverErrors)) delete serverErrors[key]
}

function openCreate(): void {
  form.name = ''
  form.phone = ''
  form.role = 'teacher'
  form.password = randomPassword()
  form.teacher_id = ''
  clearServerErrors()
  dialogVisible.value = true
}

function randomPassword(): string {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789'
  let out = ''
  for (let i = 0; i < 12; i += 1) out += chars[Math.floor(Math.random() * chars.length)]
  return out
}

function handleRandomPassword(): void {
  form.password = randomPassword()
  ElMessage.success('已生成新密码')
}

async function copyPassword(): Promise<void> {
  try {
    await navigator.clipboard.writeText(form.password)
    ElMessage.success('密码已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选择复制')
  }
}

async function submit(): Promise<void> {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  clearServerErrors()
  submitting.value = true
  try {
    const payload: StaffPayload = {
      name: form.name.trim(),
      phone: form.phone.trim(),
      role: form.role,
      password: form.password,
      teacher_id: form.role === 'teacher' ? form.teacher_id || null : null,
    }
    const result = await createStaff(payload)
    if (result.revived) {
      ElMessage.success('该手机号存在已停用账号，已重置并重新启用')
    } else {
      ElMessage.success('账号已创建')
    }
    dialogVisible.value = false
    reload()
  } catch (error) {
    if (error instanceof ApiError && error.fieldErrors) {
      Object.assign(serverErrors, error.fieldErrors)
    } else {
      ElMessage.error(staffErrorMessage(error))
    }
  } finally {
    submitting.value = false
  }
}

// ---------------------------------------------------------------- 新建老师档案（一步完成）

const newTeacherVisible = ref(false)
const newTeacherName = ref('')
const creatingTeacher = ref(false)

function openNewTeacher(): void {
  newTeacherName.value = ''
  newTeacherVisible.value = true
}

async function submitNewTeacher(): Promise<void> {
  const name = newTeacherName.value.trim()
  if (!name) {
    ElMessage.warning('请输入老师姓名')
    return
  }
  creatingTeacher.value = true
  try {
    const teacher = await createTeacher({ name })
    await fetchAllTeachers()
    form.teacher_id = teacher.id
    newTeacherVisible.value = false
    ElMessage.success('老师档案已创建并关联')
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '创建失败')
  } finally {
    creatingTeacher.value = false
  }
}

// ---------------------------------------------------------------- 停用 / 启用

async function changeStatus(row: Staff, status: StaffStatus): Promise<void> {
  const isDisable = status === 'inactive'
  try {
    await ElMessageBox.confirm(
      isDisable
        ? `停用「${row.name}」后将立即无法登录，历史记录保留。确认停用？`
        : `确认恢复「${row.name}」为启用？`,
      isDisable ? '停用账号' : '启用账号',
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
    await updateStaff(row.id, { status })
    ElMessage.success(isDisable ? '已停用' : '已启用')
    reload()
  } catch (error) {
    ElMessage.error(staffErrorMessage(error))
  }
}

// ---------------------------------------------------------------- 重置密码

const resetVisible = ref(false)
const resetTarget = ref<Staff | null>(null)
const resetForm = reactive({ password: '' })
const resetting = ref(false)
const resetRef = ref<FormInstance>()

function openReset(row: Staff): void {
  resetTarget.value = row
  resetForm.password = ''
  resetVisible.value = true
}

async function submitReset(): Promise<void> {
  if (!resetRef.value || !resetTarget.value) return
  const valid = await resetRef.value.validate().catch(() => false)
  if (!valid) return

  resetting.value = true
  try {
    await updateStaff(resetTarget.value.id, { password: resetForm.password })
    ElMessage.success('密码已重置')
    resetVisible.value = false
  } catch (error) {
    ElMessage.error(staffErrorMessage(error))
  } finally {
    resetting.value = false
  }
}

onMounted(() => {
  reload()
})
</script>

<template>
  <div class="page">
    <el-card shadow="never" class="page__card">
      <div class="page__head">
        <div class="page__actions">
          <el-button type="primary" @click="openCreate">新建账号</el-button>
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

      <el-table v-loading="loading" :data="items" border stripe>
        <template #empty>
          <el-empty description="还没有员工账号">
            <el-button type="primary" @click="openCreate">新建第一个账号</el-button>
          </el-empty>
        </template>
        <el-table-column prop="name" label="姓名" min-width="120" />
        <el-table-column prop="phone" label="手机号" width="140" />
        <el-table-column label="角色" width="130">
          <template #default="{ row }">{{ ROLE_LABELS[row.role as StaffRole] }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'inactive' ? 'info' : 'success'" size="small">
              {{ STAFF_STATUS_LABELS[row.status as StaffStatus] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="关联老师档案" min-width="130">
          <template #default="{ row }">{{ teacherName(row.teacher_id) }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openReset(row)">重置密码</el-button>
            <el-button
              v-if="row.status === 'active'"
              link
              type="danger"
              @click="changeStatus(row, 'inactive')"
            >
              停用
            </el-button>
            <el-button v-else link type="primary" @click="changeStatus(row, 'active')">
              启用
            </el-button>
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

    <!-- 新建账号 -->
    <el-dialog v-model="dialogVisible" title="新建员工账号" width="500px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="姓名" prop="name" :error="serverErrors.name">
          <el-input
            v-model="form.name"
            maxlength="64"
            placeholder="必填"
            @input="delete serverErrors.name"
          />
        </el-form-item>
        <el-form-item label="手机号" prop="phone" :error="serverErrors.phone">
          <el-input
            v-model="form.phone"
            maxlength="11"
            placeholder="11 位手机号，必填"
            @input="delete serverErrors.phone"
          />
        </el-form-item>
        <el-form-item label="角色" prop="role">
          <el-radio-group v-model="form.role">
            <el-radio-button v-for="role in STAFF_ROLES" :key="role" :value="role">
              {{ ROLE_LABELS[role] }}
            </el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="初始密码" prop="password">
          <div class="password-row">
            <el-input v-model="form.password" maxlength="72" placeholder="8~72 字符" />
            <el-button @click="handleRandomPassword">随机生成</el-button>
            <el-button text type="primary" @click="copyPassword">复制</el-button>
          </div>
        </el-form-item>
        <el-form-item v-if="form.role === 'teacher'" label="老师档案" prop="teacher_id">
          <div class="teacher-row">
            <el-select
              v-model="form.teacher_id"
              placeholder="选择关联的老师档案"
              filterable
              class="teacher-select"
            >
              <el-option
                v-for="teacher in activeTeachers"
                :key="teacher.id"
                :label="teacher.name"
                :value="teacher.id"
              />
            </el-select>
            <el-button @click="openNewTeacher">新建档案</el-button>
          </div>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">创建</el-button>
      </template>
    </el-dialog>

    <!-- 新建老师档案 -->
    <el-dialog v-model="newTeacherVisible" title="新建老师档案" width="420px">
      <el-form label-width="80px">
        <el-form-item label="姓名">
          <el-input v-model="newTeacherName" maxlength="64" placeholder="老师姓名" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="newTeacherVisible = false">取消</el-button>
        <el-button type="primary" :loading="creatingTeacher" @click="submitNewTeacher">
          创建并关联
        </el-button>
      </template>
    </el-dialog>

    <!-- 重置密码 -->
    <el-dialog v-model="resetVisible" title="重置密码" width="420px">
      <p v-if="resetTarget" class="reset-hint">为「{{ resetTarget.name }}」设置新密码</p>
      <el-form ref="resetRef" :model="resetForm" label-width="80px">
        <el-form-item
          label="新密码"
          prop="password"
          :rules="[
            { required: true, message: '请输入新密码', trigger: 'blur' },
            { min: 8, max: 72, message: '密码 8~72 个字符', trigger: 'blur' },
          ]"
        >
          <el-input
            v-model="resetForm.password"
            maxlength="72"
            placeholder="8~72 字符"
            show-password
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="resetVisible = false">取消</el-button>
        <el-button type="primary" :loading="resetting" @click="submitReset">确认重置</el-button>
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

.password-row {
  display: flex;
  gap: 8px;
  width: 100%;
}

.teacher-row {
  display: flex;
  gap: 8px;
  width: 100%;
}

.teacher-select {
  flex: 1;
}

.reset-hint {
  margin: 0 0 12px;
  color: #606266;
  font-size: 13px;
}
</style>
