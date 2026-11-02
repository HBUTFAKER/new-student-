<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'

import { getStudent } from '@/api/archive'
import { listClasses, listClassStudents, listCourses } from '@/api/course'
import { ApiError } from '@/api/http'
import { createAdjustment, createPurchase, getBalance, listLedger } from '@/api/ledger'
import { createRefund, createTransfer } from '@/api/trade'
import PageHeader from '@/components/PageHeader.vue'
import { useAuthStore } from '@/stores/auth'
import type { Student } from '@/types/archive'
import type { ClassItem, Course } from '@/types/course'
import type { Balance, LedgerEntry, PackageView } from '@/types/ledger'
import {
  LEDGER_TYPE_LABELS,
  LEDGER_TYPE_TAGS,
  PACKAGE_STATUS_LABELS,
  PACKAGE_STATUS_TAGS,
} from '@/types/ledger'
import { formatDate, formatDateTime } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const studentId = computed(() => String(route.params.id))
const isOffice = computed(() => auth.role === 'admin' || auth.role === 'registrar')

const student = ref<Student | null>(null)
const balance = ref<Balance | null>(null)
const loadingBalance = ref(false)

const ledger = ref<LedgerEntry[]>([])
const ledgerTotal = ref(0)
const ledgerPage = ref(1)
const ledgerPageSize = ref(20)
const loadingLedger = ref(false)
const ledgerError = ref('')

// ---------------------------------------------------------------- 取数

async function loadStudent(): Promise<void> {
  try {
    student.value = await getStudent(studentId.value)
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '学生信息加载失败')
  }
}

async function loadBalance(): Promise<void> {
  loadingBalance.value = true
  try {
    balance.value = await getBalance(studentId.value)
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '余额加载失败')
  } finally {
    loadingBalance.value = false
  }
}

async function loadLedger(): Promise<void> {
  loadingLedger.value = true
  ledgerError.value = ''
  try {
    const data = await listLedger(studentId.value, {
      page: ledgerPage.value,
      page_size: ledgerPageSize.value,
    })
    ledger.value = data.items
    ledgerTotal.value = data.total
  } catch (error) {
    ledgerError.value = error instanceof Error ? error.message : '流水加载失败'
    ledger.value = []
    ledgerTotal.value = 0
  } finally {
    loadingLedger.value = false
  }
}

function handleLedgerPageChange(next: number): void {
  ledgerPage.value = next
  void loadLedger()
}

function handleLedgerSizeChange(size: number): void {
  ledgerPageSize.value = size
  ledgerPage.value = 1
  void loadLedger()
}

function shortId(value: string | null): string {
  return value ? value.slice(0, 8) : '-'
}

function signedDelta(delta: number): string {
  return delta > 0 ? `+${delta}` : `${delta}`
}

function deltaClass(delta: number): string {
  return delta > 0 ? 'ledger__delta--in' : 'ledger__delta--out'
}

function goBack(): void {
  router.push({ name: 'archive-students' })
}

// ---------------------------------------------------------------- 下拉数据（课程 / 班级）

const courses = ref<Course[]>([])
const classes = ref<ClassItem[]>([])

async function loadOptions(): Promise<void> {
  try {
    const [courseData, classData] = await Promise.all([
      listCourses({ page: 1, page_size: 100, status: 'active' }),
      listClasses({ page: 1, page_size: 100 }),
    ])
    courses.value = courseData.items
    classes.value = classData.items
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '下拉数据加载失败')
  }
}

// ---------------------------------------------------------------- 购课登记

const purchaseVisible = ref(false)
const purchaseSubmitting = ref(false)
const purchaseRef = ref<FormInstance>()
const purchaseForm = reactive({
  course_id: '',
  lesson_count: 10,
  gift_lesson_count: 0,
  valid_months: 12,
  amount: 0,
})

const purchaseRules: FormRules<typeof purchaseForm> = {
  course_id: [{ required: true, message: '请选择课程', trigger: 'change' }],
  lesson_count: [{ required: true, message: '请输入课时数', trigger: 'blur' }],
  valid_months: [{ required: true, message: '请输入有效期（月）', trigger: 'blur' }],
}

function openPurchase(): void {
  purchaseForm.course_id = ''
  purchaseForm.lesson_count = 10
  purchaseForm.gift_lesson_count = 0
  purchaseForm.valid_months = 12
  purchaseForm.amount = 0
  purchaseVisible.value = true
}

async function submitPurchase(): Promise<void> {
  if (!purchaseRef.value) return
  const valid = await purchaseRef.value.validate().catch(() => false)
  if (!valid) return
  purchaseSubmitting.value = true
  try {
    await createPurchase({
      student_id: studentId.value,
      course_id: purchaseForm.course_id,
      lesson_count: purchaseForm.lesson_count,
      gift_lesson_count: purchaseForm.gift_lesson_count,
      valid_months: purchaseForm.valid_months,
      amount: purchaseForm.amount,
    })
    ElMessage.success('已登记购课')
    purchaseVisible.value = false
    await Promise.all([loadBalance(), loadLedger()])
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '购课登记失败')
  } finally {
    purchaseSubmitting.value = false
  }
}

// ---------------------------------------------------------------- 课时调整

const adjustVisible = ref(false)
const adjustSubmitting = ref(false)
const adjustRef = ref<FormInstance>()
const adjustForm = reactive({
  purchase_id: '',
  delta: 1,
  reason: '',
})

const adjustRules: FormRules<typeof adjustForm> = {
  purchase_id: [{ required: true, message: '请选择课时包', trigger: 'change' }],
  reason: [{ required: true, message: '原因必填', trigger: 'blur' }],
}

function openAdjust(): void {
  adjustForm.purchase_id = ''
  adjustForm.delta = 1
  adjustForm.reason = ''
  adjustVisible.value = true
}

async function submitAdjust(): Promise<void> {
  if (!adjustRef.value) return
  const valid = await adjustRef.value.validate().catch(() => false)
  if (!valid) return
  if (adjustForm.delta === 0) {
    ElMessage.warning('调整数量不能为 0')
    return
  }
  adjustSubmitting.value = true
  try {
    await createAdjustment({
      student_id: studentId.value,
      purchase_id: adjustForm.purchase_id,
      delta: adjustForm.delta,
      reason: adjustForm.reason.trim(),
    })
    ElMessage.success('已调整课时')
    adjustVisible.value = false
    await Promise.all([loadBalance(), loadLedger()])
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '课时调整失败')
  } finally {
    adjustSubmitting.value = false
  }
}

// ---------------------------------------------------------------- 退费（两步：表单 + 红色强确认）

const refundVisible = ref(false)
const refundSubmitting = ref(false)
const refundRef = ref<FormInstance>()
const refundForm = reactive({
  purchase_id: '',
  refund_lessons: 1,
  refund_amount: 0,
  reason: '',
})

const refundRules: FormRules<typeof refundForm> = {
  purchase_id: [{ required: true, message: '请选择课时包', trigger: 'change' }],
  reason: [{ required: true, message: '退费原因必填', trigger: 'blur' }],
}

const selectedPackage = computed<PackageView | null>(
  () =>
    balance.value?.packages.find((item) => item.purchase_id === refundForm.purchase_id) ?? null,
)

function openRefund(): void {
  refundForm.purchase_id = ''
  refundForm.refund_lessons = 1
  refundForm.refund_amount = 0
  refundForm.reason = ''
  refundVisible.value = true
}

// 红色强确认（1.2 拍板项）：四要素完整展示 + 动词按钮「确认退费」
const refundConfirmVisible = ref(false)

function openRefundConfirm(): void {
  if (!refundRef.value) return
  void refundRef.value.validate().then((valid) => {
    if (!valid) return
    if (!selectedPackage.value) {
      ElMessage.warning('请选择课时包')
      return
    }
    if (refundForm.refund_lessons > (selectedPackage.value.remaining || 0)) {
      ElMessage.warning(`退费课时不能超过该包剩余 ${selectedPackage.value.remaining} 课时`)
      return
    }
    refundConfirmVisible.value = true
  })
}

async function submitRefund(): Promise<void> {
  refundSubmitting.value = true
  try {
    await createRefund({
      student_id: studentId.value,
      purchase_id: refundForm.purchase_id,
      refund_lessons: refundForm.refund_lessons,
      refund_amount: refundForm.refund_amount,
      reason: refundForm.reason.trim(),
    })
    ElMessage.success('已退费')
    refundConfirmVisible.value = false
    refundVisible.value = false
    await Promise.all([loadBalance(), loadLedger()])
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '退费失败')
  } finally {
    refundSubmitting.value = false
  }
}

// ---------------------------------------------------------------- 转班

const transferVisible = ref(false)
const transferSubmitting = ref(false)
const transferFromId = ref('')
const transferToId = ref('')
const transferLoading = ref(false)
const studentClasses = ref<ClassItem[]>([])

const transferToOptions = computed(() => {
  const from = classes.value.find((item) => item.id === transferFromId.value)
  if (!from) return []
  return classes.value.filter(
    (item) => item.id !== from.id && item.course_id === from.course_id && item.status === 'active',
  )
})

async function openTransfer(): Promise<void> {
  transferFromId.value = ''
  transferToId.value = ''
  transferVisible.value = true
  await loadStudentClasses()
}

/** 找出学生当前所在班：遍历班级名单，班级量小可接受 */
async function loadStudentClasses(): Promise<void> {
  transferLoading.value = true
  try {
    const found: ClassItem[] = []
    await Promise.all(
      classes.value.map(async (klass) => {
        const roster = await listClassStudents(klass.id)
        if (roster.items.some((item) => item.student_id === studentId.value)) {
          found.push(klass)
        }
      }),
    )
    studentClasses.value = found
    if (found.length > 0) transferFromId.value = found[0].id
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '学生班级加载失败')
  } finally {
    transferLoading.value = false
  }
}

async function submitTransfer(): Promise<void> {
  if (!transferFromId.value) {
    ElMessage.warning('请选择转出班级')
    return
  }
  if (!transferToId.value) {
    ElMessage.warning('请选择转入班级')
    return
  }
  transferSubmitting.value = true
  try {
    await createTransfer({
      student_id: studentId.value,
      from_class_id: transferFromId.value,
      to_class_id: transferToId.value,
    })
    ElMessage.success('已转班')
    transferVisible.value = false
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '转班失败')
  } finally {
    transferSubmitting.value = false
  }
}

// ---------------------------------------------------------------- 初始化

onMounted(async () => {
  await Promise.all([loadStudent(), loadBalance(), loadLedger(), loadOptions()])
})
</script>

<template>
  <div class="page">
    <el-card shadow="never" class="page__card">
      <PageHeader
        :title="`${student?.name ?? '学生'} · 课时账本`"
        subtitle="课时余额、课时包与流水只增不可改，操作均留痕"
      >
        <el-button @click="goBack">返回列表</el-button>
        <template v-if="isOffice">
          <el-button type="primary" @click="openPurchase">购课登记</el-button>
          <el-button @click="openAdjust">课时调整</el-button>
          <el-button type="warning" plain @click="openRefund">退费</el-button>
          <el-button @click="openTransfer">转班</el-button>
        </template>
      </PageHeader>

      <!-- 余额卡 -->
      <div v-loading="loadingBalance" class="ledger__balance">
        <div class="ledger__balance-total">
          <span class="ledger__balance-label">实时余额</span>
          <span class="ledger__balance-num">{{ balance?.total ?? 0 }}</span>
          <span class="ledger__balance-unit">课时</span>
        </div>
        <div class="ledger__packages">
          <div
            v-for="item in balance?.packages ?? []"
            :key="item.purchase_id"
            class="ledger__package"
          >
            <div class="ledger__package-head">
              <span class="ledger__package-name">{{ item.package_name }}</span>
              <el-tag :type="PACKAGE_STATUS_TAGS[item.status]" size="small" effect="plain">
                {{ PACKAGE_STATUS_LABELS[item.status] }}
              </el-tag>
            </div>
            <div class="ledger__package-meta">
              {{ item.course_name }} · 剩余
              <strong>{{ item.remaining }}</strong>
              / {{ item.granted }} 课时
            </div>
            <div class="ledger__package-meta">
              有效期至 {{ item.expires_at ? formatDate(item.expires_at) : '长期' }}
            </div>
          </div>
          <el-empty
            v-if="!loadingBalance && (balance?.packages ?? []).length === 0"
            description="还没有课时包"
            :image-size="60"
          />
        </div>
      </div>

      <!-- 流水表 -->
      <el-alert v-if="ledgerError" :title="ledgerError" type="error" show-icon :closable="false" class="page__error" />

      <el-table v-loading="loadingLedger" :data="ledger" border stripe empty-text="暂无流水">
        <el-table-column label="日期" width="160">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="类型" width="110">
          <template #default="{ row }">
            <el-tag :type="LEDGER_TYPE_TAGS[row.type as keyof typeof LEDGER_TYPE_TAGS]" size="small" effect="plain">
              {{ LEDGER_TYPE_LABELS[row.type as keyof typeof LEDGER_TYPE_LABELS] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="变动" width="90" align="right">
          <template #default="{ row }">
            <span :class="deltaClass(row.delta)">{{ signedDelta(row.delta) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="备注 / 原因" min-width="220">
          <template #default="{ row }">{{ row.reason || '-' }}</template>
        </el-table-column>
        <el-table-column label="经办人" width="120">
          <template #default="{ row }">{{ shortId(row.operator_id) }}</template>
        </el-table-column>
      </el-table>

      <div class="page__pager">
        <el-pagination
          :current-page="ledgerPage"
          :page-size="ledgerPageSize"
          :total="ledgerTotal"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          background
          @current-change="handleLedgerPageChange"
          @size-change="handleLedgerSizeChange"
        />
      </div>
    </el-card>

    <!-- 购课登记 -->
    <el-dialog v-model="purchaseVisible" title="购课登记" width="480px">
      <el-form ref="purchaseRef" :model="purchaseForm" :rules="purchaseRules" label-width="90px">
        <el-form-item label="课程" prop="course_id">
          <el-select v-model="purchaseForm.course_id" placeholder="选择课程" filterable style="width: 100%">
            <el-option v-for="course in courses" :key="course.id" :label="course.name" :value="course.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="课时数" prop="lesson_count">
          <el-input-number v-model="purchaseForm.lesson_count" :min="1" :max="10000" style="width: 100%" />
        </el-form-item>
        <el-form-item label="赠课数">
          <el-input-number v-model="purchaseForm.gift_lesson_count" :min="0" :max="10000" style="width: 100%" />
        </el-form-item>
        <el-form-item label="有效期(月)" prop="valid_months">
          <el-input-number v-model="purchaseForm.valid_months" :min="1" :max="120" style="width: 100%" />
        </el-form-item>
        <el-form-item label="实收金额">
          <el-input-number v-model="purchaseForm.amount" :min="0" :precision="2" :step="100" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="purchaseVisible = false">取消</el-button>
        <el-button type="primary" :loading="purchaseSubmitting" @click="submitPurchase">确认购课</el-button>
      </template>
    </el-dialog>

    <!-- 课时调整 -->
    <el-dialog v-model="adjustVisible" title="课时调整" width="480px">
      <el-alert
        title="正数为加课时，负数为扣课时；原因必填，独立流水留痕"
        type="info"
        show-icon
        :closable="false"
        class="dialog__tip"
      />
      <el-form ref="adjustRef" :model="adjustForm" :rules="adjustRules" label-width="90px">
        <el-form-item label="课时包" prop="purchase_id">
          <el-select v-model="adjustForm.purchase_id" placeholder="选择课时包" style="width: 100%">
            <el-option
              v-for="item in balance?.packages ?? []"
              :key="item.purchase_id"
              :label="`${item.package_name}（剩余 ${item.remaining}）`"
              :value="item.purchase_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="调整数量" prop="delta">
          <el-input-number v-model="adjustForm.delta" :min="-10000" :max="10000" style="width: 100%" />
        </el-form-item>
        <el-form-item label="原因" prop="reason">
          <el-input v-model="adjustForm.reason" type="textarea" :rows="2" maxlength="255" placeholder="必填" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="adjustVisible = false">取消</el-button>
        <el-button type="primary" :loading="adjustSubmitting" @click="submitAdjust">确认调整</el-button>
      </template>
    </el-dialog>

    <!-- 退费（第一步：表单） -->
    <el-dialog v-model="refundVisible" title="退费" width="480px">
      <el-form ref="refundRef" :model="refundForm" :rules="refundRules" label-width="90px">
        <el-form-item label="课时包" prop="purchase_id">
          <el-select v-model="refundForm.purchase_id" placeholder="选择要退的课时包" style="width: 100%">
            <el-option
              v-for="item in balance?.packages ?? []"
              :key="item.purchase_id"
              :label="`${item.package_name}（剩余 ${item.remaining}）`"
              :value="item.purchase_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="退课时数" prop="refund_lessons">
          <el-input-number
            v-model="refundForm.refund_lessons"
            :min="1"
            :max="selectedPackage?.remaining ?? 10000"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="退费金额">
          <el-input-number v-model="refundForm.refund_amount" :min="0" :precision="2" :step="100" style="width: 100%" />
        </el-form-item>
        <el-form-item label="原因" prop="reason">
          <el-input v-model="refundForm.reason" type="textarea" :rows="2" maxlength="255" placeholder="退费原因必填" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="refundVisible = false">取消</el-button>
        <el-button type="danger" :loading="refundSubmitting" @click="openRefundConfirm">下一步：确认退费</el-button>
      </template>
    </el-dialog>

    <!-- 退费（第二步：红色强确认） -->
    <el-dialog v-model="refundConfirmVisible" title="退费确认" width="440px" :close-on-click-modal="false">
      <div class="refund-confirm">
        <div class="refund-confirm__warn">
          退费会扣减该课时包余额并写入不可修改的流水，请核对以下信息，确认无误后再执行。
        </div>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="学生">{{ student?.name ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="课时包">{{ selectedPackage?.package_name ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="退课时数">
            <strong class="refund-confirm__num">{{ refundForm.refund_lessons }}</strong> 课时
          </el-descriptions-item>
          <el-descriptions-item label="退费金额">
            <strong class="refund-confirm__num">¥{{ refundForm.refund_amount }}</strong>
          </el-descriptions-item>
        </el-descriptions>
      </div>
      <template #footer>
        <el-button @click="refundConfirmVisible = false">取消</el-button>
        <el-button type="danger" :loading="refundSubmitting" @click="submitRefund">确认退费</el-button>
      </template>
    </el-dialog>

    <!-- 转班 -->
    <el-dialog v-model="transferVisible" title="转班" width="480px">
      <el-form label-width="90px" v-loading="transferLoading">
        <el-form-item label="转出班级" required>
          <el-select v-model="transferFromId" placeholder="学生当前所在班" style="width: 100%">
            <el-option v-for="item in studentClasses" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="转入班级" required>
          <el-select v-model="transferToId" placeholder="同课程目标班" style="width: 100%">
            <el-option v-for="item in transferToOptions" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="transferVisible = false">取消</el-button>
        <el-button type="primary" :loading="transferSubmitting" @click="submitTransfer">确认转班</el-button>
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
  border-radius: var(--app-radius-card);
}

.page__error {
  margin-bottom: 12px;
}

.page__pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.dialog__tip {
  margin-bottom: 12px;
}

/* 余额卡 */
.ledger__balance {
  display: flex;
  gap: 20px;
  align-items: stretch;
  margin-bottom: 16px;
}

.ledger__balance-total {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: flex-start;
  gap: 4px;
  min-width: 200px;
  padding: 20px 24px;
  border-radius: var(--app-radius-card);
  background: linear-gradient(135deg, var(--el-color-primary), var(--el-color-primary-light-3));
  color: var(--el-bg-color);
}

.ledger__balance-label {
  font-size: 13px;
  opacity: 0.9;
}

.ledger__balance-num {
  font-size: 40px;
  font-weight: 700;
  line-height: 1;
}

.ledger__balance-unit {
  font-size: 13px;
  opacity: 0.9;
}

.ledger__packages {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 12px;
  flex: 1;
}

.ledger__package {
  padding: 14px 16px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--app-radius-base);
  background-color: var(--el-bg-color);
}

.ledger__package-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.ledger__package-name {
  font-weight: 600;
  font-size: 14px;
  color: var(--el-text-color-primary);
}

.ledger__package-meta {
  font-size: 12px;
  color: var(--el-text-color-regular);
  line-height: 1.7;
}

/* 流水增减配色 */
.ledger__delta--in {
  color: var(--el-color-success);
  font-weight: 600;
}

.ledger__delta--out {
  color: var(--el-color-danger);
  font-weight: 600;
}

/* 退费强确认 */
.refund-confirm__warn {
  margin-bottom: 14px;
  padding: 10px 12px;
  border-radius: var(--app-radius-base);
  background-color: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
  font-size: 13px;
  line-height: 1.6;
}

.refund-confirm__num {
  color: var(--el-color-danger);
  font-size: 16px;
}
</style>
