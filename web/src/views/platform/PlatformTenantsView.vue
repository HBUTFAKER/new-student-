<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'

import {
  createTenant,
  getTenant,
  listPlans,
  listTenants as fetchTenants,
  renewTenant,
} from '@/api/platform'
import { ApiError } from '@/api/http'
import { usePagedList } from '@/composables/usePagedList'
import type {
  PlanItem,
  TenantCreatePayload,
  TenantDetail,
  TenantListItem,
} from '@/types/platform'
import { formatDate, formatDateTime } from '@/utils/format'

const { items, total, page, pageSize, loading, errorText, load, fetchAll } = usePagedList(
  fetchTenants,
  10,
)

const search = ref('')

function reload(): void {
  void load({ name: search.value.trim() || undefined })
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

// ---------------------------------------------------------------- 三卡概览

const stats = reactive({ total: 0, trial: 0, expiring: 0 })

function isExpiring(row: TenantListItem): boolean {
  // 已到期（days_left 为负）与临期（≤7 天）都算「需要关注」
  return row.days_left <= 7
}

async function loadStats(): Promise<void> {
  try {
    const all = await fetchAll({})
    stats.total = all.length
    stats.trial = all.filter((t) => t.sub_status === 'trial').length
    stats.expiring = all.filter(isExpiring).length
  } catch {
    // 概览卡拉取失败静默，表格自身有错误态
  }
}

// ---------------------------------------------------------------- 状态 tag

interface StatusTag {
  text: string
  type: 'primary' | 'success' | 'warning' | 'danger'
}

function statusInfo(row: TenantListItem): StatusTag {
  if (row.sub_status === 'expired') return { text: '已到期', type: 'danger' }
  if (row.sub_status === 'trial') return { text: '试用中', type: 'primary' }
  if (row.days_left <= 7) return { text: `临期 ${row.days_left} 天`, type: 'warning' }
  return { text: '正常', type: 'success' }
}

function percent(used: number, max: number): number {
  if (max <= 0) return 0
  return Math.min(100, Math.round((used / max) * 100))
}

// ---------------------------------------------------------------- 手动开通机构

const createVisible = ref(false)
const creating = ref(false)
const createRef = ref<FormInstance>()
const createForm = reactive({
  name: '',
  admin_name: '',
  admin_phone: '',
  admin_password: '',
})

const createRules: FormRules<typeof createForm> = {
  name: [{ required: true, message: '请输入机构名称', trigger: 'blur' }],
  admin_name: [{ required: true, message: '请输入管理员姓名', trigger: 'blur' }],
  admin_phone: [
    { required: true, message: '请输入手机号', trigger: 'blur' },
    {
      validator: (_rule, value: string, callback) => {
        if (!/^1\d{10}$/.test(value)) callback(new Error('请输入 11 位手机号'))
        else callback()
      },
      trigger: 'blur',
    },
  ],
  admin_password: [
    { required: true, message: '请输入初始密码', trigger: 'blur' },
    { min: 8, max: 128, message: '密码至少 8 位', trigger: 'blur' },
  ],
}

function openCreate(): void {
  createForm.name = ''
  createForm.admin_name = ''
  createForm.admin_phone = ''
  createForm.admin_password = ''
  createVisible.value = true
}

async function submitCreate(): Promise<void> {
  if (!createRef.value) return
  const valid = await createRef.value.validate().catch(() => false)
  if (!valid) return

  creating.value = true
  try {
    const payload: TenantCreatePayload = {
      name: createForm.name.trim(),
      admin_name: createForm.admin_name.trim(),
      admin_phone: createForm.admin_phone.trim(),
      admin_password: createForm.admin_password,
    }
    const result = await createTenant(payload)
    ElMessage.success(`机构「${result.name}」已开通，管理员账号：${result.admin_phone}`)
    createVisible.value = false
    reload()
    void loadStats()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '开通失败，请稍后重试')
  } finally {
    creating.value = false
  }
}

// ---------------------------------------------------------------- 详情抽屉

const drawerVisible = ref(false)
const detail = ref<TenantDetail | null>(null)
const detailLoading = ref(false)

async function openDetail(row: TenantListItem): Promise<void> {
  drawerVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await getTenant(row.tenant_id)
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '加载详情失败')
    drawerVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

// ---------------------------------------------------------------- 续费弹窗

const renewVisible = ref(false)
const renewing = ref(false)
const renewTarget = ref<TenantListItem | null>(null)
const plans = ref<PlanItem[]>([])
const renewDays = ref(30)
const renewPlanId = ref<string>('')

const renewQuickDays = [30, 90, 365]

async function openRenew(row: TenantListItem): Promise<void> {
  renewTarget.value = row
  renewDays.value = 30
  renewPlanId.value = row.plan_id
  renewVisible.value = true
  try {
    plans.value = await listPlans()
  } catch {
    plans.value = []
  }
}

async function submitRenew(): Promise<void> {
  if (!renewTarget.value) return
  if (renewDays.value < 1) {
    ElMessage.warning('顺延天数至少 1 天')
    return
  }
  renewing.value = true
  try {
    await renewTenant(renewTarget.value.tenant_id, {
      days: renewDays.value,
      plan_id: renewPlanId.value || null,
    })
    ElMessage.success('续费成功')
    renewVisible.value = false
    drawerVisible.value = false
    reload()
    void loadStats()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '续费失败，请稍后重试')
  } finally {
    renewing.value = false
  }
}

onMounted(() => {
  reload()
  void loadStats()
})
</script>

<template>
  <div class="platform-page">
    <!-- 概览卡 -->
    <div class="stats">
      <div class="stats__card">
        <div class="stats__label">租户总数</div>
        <div class="stats__value">{{ stats.total }}</div>
      </div>
      <div class="stats__card">
        <div class="stats__label">试用中</div>
        <div class="stats__value stats__value--primary">{{ stats.trial }}</div>
      </div>
      <div class="stats__card">
        <div class="stats__label">临期 + 已到期</div>
        <div class="stats__value stats__value--danger">{{ stats.expiring }}</div>
      </div>
    </div>

    <el-card shadow="never" class="panel">
      <div class="panel__head">
        <div class="panel__search">
          <el-input
            v-model="search"
            placeholder="搜索机构名称"
            clearable
            class="panel__search-input"
            @keyup.enter="reload"
            @clear="reload"
          />
          <el-button type="primary" plain @click="reload">搜索</el-button>
        </div>
        <el-button type="primary" @click="openCreate">手动开通机构</el-button>
      </div>

      <el-alert
        v-if="errorText"
        :title="errorText"
        type="error"
        show-icon
        :closable="false"
        class="panel__error"
      />

      <el-table v-loading="loading" :data="items" border stripe>
        <template #empty><el-empty description="暂无租户" /></template>
        <el-table-column prop="name" label="机构名" min-width="160" />
        <el-table-column prop="plan_name" label="套餐" width="100" />
        <el-table-column label="到期日" width="120">
          <template #default="{ row }">{{ formatDate(row.end_date) }}</template>
        </el-table-column>
        <el-table-column label="剩余天数" width="120">
          <template #default="{ row }">
            <el-tag :type="statusInfo(row).type" size="small">{{ statusInfo(row).text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="students_used" label="在档学生" width="100" />
        <el-table-column prop="messages_used" label="本月消息" width="100" />
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row)">详情</el-button>
            <el-button link type="warning" @click="openRenew(row)">续费</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="panel__pager">
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

    <!-- 手动开通机构 -->
    <el-dialog v-model="createVisible" title="手动开通机构" width="480px">
      <el-form ref="createRef" :model="createForm" :rules="createRules" label-width="90px">
        <el-form-item label="机构名称" prop="name">
          <el-input v-model="createForm.name" maxlength="128" placeholder="必填" />
        </el-form-item>
        <el-form-item label="管理员姓名" prop="admin_name">
          <el-input v-model="createForm.admin_name" maxlength="64" placeholder="必填" />
        </el-form-item>
        <el-form-item label="手机号" prop="admin_phone">
          <el-input v-model="createForm.admin_phone" maxlength="11" placeholder="11 位手机号，必填" />
        </el-form-item>
        <el-form-item label="初始密码" prop="admin_password">
          <el-input
            v-model="createForm.admin_password"
            maxlength="128"
            placeholder="至少 8 位"
            show-password
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">开通</el-button>
      </template>
    </el-dialog>

    <!-- 租户详情抽屉 -->
    <el-drawer v-model="drawerVisible" title="租户详情" size="440px">
      <div v-loading="detailLoading" class="detail">
        <template v-if="detail">
          <div class="detail__name">{{ detail.name }}</div>

          <div class="detail__section">
            <div class="detail__section-title">订阅信息</div>
            <div class="detail__row">
              <span class="detail__label">套餐</span>
              <span class="detail__value">{{ detail.plan_name }}</span>
            </div>
            <div class="detail__row">
              <span class="detail__label">起止</span>
              <span class="detail__value">
                {{ formatDate(detail.start_date) }} ~ {{ formatDate(detail.end_date) }}
              </span>
            </div>
            <div class="detail__row">
              <span class="detail__label">状态</span>
              <el-tag :type="statusInfo(detail).type" size="small">{{ statusInfo(detail).text }}</el-tag>
            </div>
          </div>

          <div class="detail__section">
            <div class="detail__section-title">用量（{{ detail.period }}）</div>
            <div class="detail__usage">
              <div class="detail__usage-label">
                在档学生 {{ detail.students_used }} / {{ detail.max_students }}
              </div>
              <el-progress
                :percentage="percent(detail.students_used, detail.max_students)"
                :stroke-width="10"
              />
            </div>
            <div class="detail__usage">
              <div class="detail__usage-label">
                本月消息 {{ detail.messages_used }} / {{ detail.max_messages }}
              </div>
              <el-progress
                :percentage="percent(detail.messages_used, detail.max_messages)"
                :stroke-width="10"
              />
            </div>
          </div>

          <div class="detail__section">
            <div class="detail__section-title">管理员联系方式</div>
            <div class="detail__row">
              <span class="detail__label">姓名</span>
              <span class="detail__value">{{ detail.admin_name ?? '-' }}</span>
            </div>
            <div class="detail__row">
              <span class="detail__label">手机号</span>
              <span class="detail__value">{{ detail.admin_phone ?? '-' }}</span>
            </div>
          </div>

          <el-button type="warning" class="detail__renew" @click="openRenew(detail)">
            续费
          </el-button>
        </template>
      </div>
    </el-drawer>

    <!-- 续费弹窗 -->
    <el-dialog v-model="renewVisible" title="续费" width="440px">
      <p v-if="renewTarget" class="renew-hint">
        为「{{ renewTarget.name }}」顺延订阅时长
      </p>
      <el-form label-width="90px">
        <el-form-item label="顺延天数">
          <div class="renew-days">
            <el-button
              v-for="d in renewQuickDays"
              :key="d"
              :type="renewDays === d ? 'primary' : 'default'"
              @click="renewDays = d"
            >
              {{ d }} 天
            </el-button>
            <el-input-number v-model="renewDays" :min="1" :max="3650" class="renew-days__custom" />
          </div>
        </el-form-item>
        <el-form-item label="换套餐">
          <el-select v-model="renewPlanId" placeholder="保持当前套餐" clearable class="renew-plan">
            <el-option
              v-for="plan in plans"
              :key="plan.id"
              :label="`${plan.name}（学生 ${plan.max_students} / 消息 ${plan.max_messages}）`"
              :value="plan.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="renewVisible = false">取消</el-button>
        <el-button type="primary" :loading="renewing" @click="submitRenew">确认续费</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.platform-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}

.stats__card {
  padding: 20px 24px;
  background-color: var(--el-bg-color);
  border-radius: 12px;
  border: 1px solid var(--app-platform-card-border);
}

.stats__label {
  font-size: 13px;
  color: var(--app-platform-label);
}

.stats__value {
  margin-top: 8px;
  font-size: 30px;
  font-weight: 700;
  color: var(--app-platform-heading);
}

.stats__value--primary {
  color: var(--app-platform-primary);
}

.stats__value--danger {
  color: var(--app-platform-danger);
}

.panel {
  border-radius: 12px;
  border: 1px solid var(--app-platform-card-border);
}

.panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.panel__search {
  display: flex;
  gap: 8px;
}

.panel__search-input {
  width: 240px;
}

.panel__error {
  margin-bottom: 12px;
}

.panel__pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.detail {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 200px;
}

.detail__name {
  font-size: 20px;
  font-weight: 700;
  margin-bottom: 8px;
}

.detail__section {
  padding: 16px 0;
  border-bottom: 1px solid var(--app-platform-divider);
}

.detail__section:last-of-type {
  border-bottom: none;
}

.detail__section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 12px;
}

.detail__row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
  font-size: 14px;
}

.detail__label {
  width: 48px;
  color: var(--el-text-color-secondary);
}

.detail__value {
  color: var(--el-text-color-primary);
}

.detail__usage {
  margin-bottom: 12px;
}

.detail__usage-label {
  font-size: 13px;
  color: var(--el-text-color-regular);
  margin-bottom: 6px;
}

.detail__renew {
  margin-top: 8px;
}

.renew-hint {
  margin: 0 0 16px;
  color: var(--el-text-color-regular);
  font-size: 13px;
}

.renew-days {
  display: flex;
  align-items: center;
  gap: 8px;
}

.renew-days__custom {
  width: 140px;
}

.renew-plan {
  width: 100%;
}
</style>
