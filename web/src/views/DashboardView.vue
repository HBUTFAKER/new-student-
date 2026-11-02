<script setup lang="ts">
/**
 * 工作台（7.7 重做）：顶部欢迎语 + 关键数字卡 + 快捷入口。
 *
 * 数字卡全部来自现有接口，不新增后端，且按角色权限分发：
 * - office（admin/registrar）：今日课时（/lessons）、今日到课人次（/reports/attendance-summary）、
 *   本月消耗（/reports/consumption）、在档学生（/students）；
 * - teacher：在档学生（/students）、班级数（/classes）——teacher 无权访问 /lessons 与 /reports，
 *   不能去调否则触发 403 错误提示。
 * 每张卡独立 try/catch 静默降级为「—」，一张失败不拖垮整页。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { listStudents } from '@/api/archive'
import { listClasses } from '@/api/course'
import { listAllLessons } from '@/api/lesson'
import { getAttendanceSummary, getConsumption } from '@/api/report'
import { menuForRole } from '@/config/menu'
import { useAuthStore } from '@/stores/auth'
import { ROLE_LABELS } from '@/types/auth'
import { monthToDateRange, toWindowIso } from '@/utils/report'
import { businessNowMinutes, businessToday } from '@/utils/timetable'

const auth = useAuthStore()
const router = useRouter()

const roleLabel = computed(() => (auth.role ? ROLE_LABELS[auth.role] : ''))
const menuItems = computed(() => menuForRole(auth.role))
const isOffice = computed(() => auth.role === 'admin' || auth.role === 'registrar')

/** 纯展示的时段问候 */
const greeting = computed(() => {
  const minutes = businessNowMinutes(businessToday())
  if (minutes === null) return '你好'
  const hour = Math.floor(minutes / 60)
  if (hour < 6) return '夜深了'
  if (hour < 12) return '早上好'
  if (hour < 14) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

// ---------------------------------------------------------------- 数字卡

const today = businessToday()

interface StatCard {
  key: string
  label: string
  value: number | null
  hint: string
}

const lessonCount = ref<number | null>(null)
const presentCount = ref<number | null>(null)
const consumedCount = ref<number | null>(null)
const studentCount = ref<number | null>(null)
const classCount = ref<number | null>(null)

const cards = computed<StatCard[]>(() => {
  if (!isOffice.value) {
    return [
      { key: 'students', label: '在档学生', value: studentCount.value, hint: '当前在读学生总数' },
      { key: 'classes', label: '班级数', value: classCount.value, hint: '机构当前班级数' },
    ]
  }
  return [
    { key: 'lessons', label: '今日课时', value: lessonCount.value, hint: '今天排了多少节课' },
    { key: 'present', label: '今日到课人次', value: presentCount.value, hint: '今天到课的累计人次' },
    { key: 'consumed', label: '本月消耗课时', value: consumedCount.value, hint: '本月 1 号至今' },
    { key: 'students', label: '在档学生', value: studentCount.value, hint: '当前在读学生总数' },
  ]
})

async function loadTodayLessons(): Promise<void> {
  try {
    const lessons = await listAllLessons({ from: today, to: today })
    lessonCount.value = lessons.filter((item) => item.status !== 'cancelled').length
  } catch {
    /* 静默降级 */
  }
}

async function loadTodayPresent(): Promise<void> {
  try {
    const summary = await getAttendanceSummary({
      ...toWindowIso({ start: today, end: today }),
      detail: false,
    })
    presentCount.value = summary.items.reduce((sum, row) => sum + row.present, 0)
  } catch {
    /* 静默降级 */
  }
}

async function loadMonthConsumed(): Promise<void> {
  try {
    const report = await getConsumption(toWindowIso(monthToDateRange()))
    consumedCount.value = report.items.reduce((sum, row) => sum + row.total_consumed, 0)
  } catch {
    /* 静默降级 */
  }
}

async function loadActiveStudents(): Promise<void> {
  try {
    const data = await listStudents({ status: 'active', page: 1, page_size: 1 })
    studentCount.value = data.total
  } catch {
    /* 静默降级 */
  }
}

async function loadClassCount(): Promise<void> {
  try {
    const data = await listClasses({ page: 1, page_size: 1 })
    classCount.value = data.total
  } catch {
    /* 静默降级 */
  }
}

// ---------------------------------------------------------------- 快捷入口

interface QuickAction {
  title: string
  desc: string
  icon: string
  path: string
}

const quickActions = computed<QuickAction[]>(() => {
  if (auth.role === 'teacher') {
    return [
      { title: '我的课表', desc: '查看本人任教课次', icon: 'Calendar', path: '/my-schedule' },
      { title: '签到', desc: '给学生点名', icon: 'Checked', path: '/attendance' },
      { title: '我的班级', desc: '查看任教班级', icon: 'Collection', path: '/my-classes' },
    ]
  }
  return [
    { title: '排课日历', desc: '周/月视图排课与调课', icon: 'Calendar', path: '/scheduling/timetable' },
    { title: '签到', desc: '给学生点名', icon: 'Checked', path: '/attendance' },
    { title: '学生档案', desc: '管理在读学生', icon: 'User', path: '/archive/students' },
  ]
})

onMounted(() => {
  if (isOffice.value) {
    void loadTodayLessons()
    void loadTodayPresent()
    void loadMonthConsumed()
  }
  void loadActiveStudents()
  if (!isOffice.value) void loadClassCount()
})
</script>

<template>
  <div class="dashboard">
    <!-- 欢迎语 -->
    <section class="dashboard__hero">
      <div>
        <h2 class="dashboard__hello">{{ greeting }}，{{ auth.account }}</h2>
        <p class="dashboard__sub">
          {{ roleLabel }} · 今天是 {{ today }}，从这里开始一天的工作
        </p>
      </div>
    </section>

    <!-- 关键数字 -->
    <section class="dashboard__stats" :class="{ 'is-two': !isOffice }">
      <div v-for="card in cards" :key="card.key" class="stat">
        <div class="stat__label">{{ card.label }}</div>
        <div class="stat__value">{{ card.value === null ? '—' : card.value }}</div>
        <div class="stat__hint">{{ card.hint }}</div>
      </div>
    </section>

    <!-- 快捷入口 -->
    <section class="dashboard__section">
      <h3 class="dashboard__section-title">快捷入口</h3>
      <div class="dashboard__quick">
        <button
          v-for="action in quickActions"
          :key="action.path"
          type="button"
          class="quick"
          @click="router.push(action.path)"
        >
          <span class="quick__icon">
            <el-icon><component :is="action.icon" /></el-icon>
          </span>
          <span class="quick__text">
            <span class="quick__title">{{ action.title }}</span>
            <span class="quick__desc">{{ action.desc }}</span>
          </span>
        </button>
      </div>
    </section>

    <!-- 可进入页面（按角色） -->
    <section v-if="menuItems.length > 0" class="dashboard__section">
      <h3 class="dashboard__section-title">全部功能</h3>
      <div class="dashboard__menu">
        <template v-for="item in menuItems" :key="item.path">
          <el-button v-if="!item.children" plain class="dashboard__menu-btn" @click="router.push(item.path)">
            <el-icon><component :is="item.icon" /></el-icon>
            <span>{{ item.title }}</span>
          </el-button>
          <template v-else>
            <el-button
              v-for="child in item.children"
              :key="child.path"
              plain
              class="dashboard__menu-btn"
              @click="router.push(child.path)"
            >
              <el-icon><component :is="child.icon" /></el-icon>
              <span>{{ child.title }}</span>
            </el-button>
          </template>
        </template>
      </div>
    </section>
  </div>
</template>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: var(--app-space-5);
}

.dashboard__hero {
  padding: var(--app-space-6) var(--app-space-6);
  border-radius: var(--app-radius-card);
  background: linear-gradient(120deg, var(--el-color-primary-light-8) 0%, var(--el-bg-color) 60%);
  border: 1px solid var(--el-border-color-lighter);
}

.dashboard__hello {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.dashboard__sub {
  margin: var(--app-space-2) 0 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.dashboard__stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--app-space-4);
}

.stat {
  padding: var(--app-space-5) var(--app-space-6);
  background-color: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--app-radius-card);
  box-shadow: var(--app-shadow-card);
}

.stat__label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.stat__value {
  margin-top: var(--app-space-2);
  font-size: 30px;
  font-weight: 700;
  line-height: 1.2;
  color: var(--el-text-color-primary);
  font-variant-numeric: tabular-nums;
}

.stat__hint {
  margin-top: var(--app-space-1);
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.dashboard__section-title {
  margin: 0 0 var(--app-space-3);
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.dashboard__quick {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--app-space-4);
}

.quick {
  display: flex;
  align-items: center;
  gap: var(--app-space-3);
  padding: var(--app-space-4) var(--app-space-5);
  background-color: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--app-radius-card);
  box-shadow: var(--app-shadow-card);
  cursor: pointer;
  text-align: left;
  font: inherit;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.quick:hover {
  border-color: var(--el-color-primary-light-5);
  box-shadow: var(--app-shadow-popover);
}

.quick__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: var(--app-radius-base);
  background-color: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-size: 20px;
  flex-shrink: 0;
}

.quick__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.quick__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.quick__desc {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.dashboard__menu {
  display: flex;
  flex-wrap: wrap;
  gap: var(--app-space-2);
}

.dashboard__menu-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

/* teacher 只有 2 张卡 */
.dashboard__stats.is-two {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

@media (max-width: 1200px) {
  .dashboard__stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
