<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { listClassrooms, listTeachers } from '@/api/archive'
import { listClasses } from '@/api/course'
import { ApiError } from '@/api/http'
import { createLesson, listAllLessons, patchLesson } from '@/api/lesson'
import type { Classroom, Teacher } from '@/types/archive'
import type { ClassItem } from '@/types/course'
import type { ConflictItem, ConflictRow, ConflictType, Lesson } from '@/types/lesson'
import { LESSON_STATUS_LABELS } from '@/types/lesson'
import {
  addDays,
  businessNowMinutes,
  businessToday,
  clockOfMinutes,
  datePart,
  durationMinutes,
  formatMonthDay,
  isoAt,
  minutesOfDay,
  monthGrid,
  snapMinutes,
  startOfWeek,
  timePart,
  WEEKDAY_LABELS,
  weekDates,
} from '@/utils/timetable'
import ConflictDialog from './components/ConflictDialog.vue'
import LessonEditorDialog from './components/LessonEditorDialog.vue'

const today = businessToday()

const viewMode = ref<'week' | 'month'>('week')
const anchor = ref(today)
const classId = ref('')
const teacherId = ref('')

const lessons = ref<Lesson[]>([])
const loading = ref(false)

const classes = ref<ClassItem[]>([])
const classrooms = ref<Classroom[]>([])
const teachers = ref<Teacher[]>([])

// ---------------------------------------------------------------- 窗口与取数

const windowFrom = computed(() =>
  viewMode.value === 'week' ? startOfWeek(anchor.value) : monthGrid(anchor.value)[0],
)

const windowTo = computed(() =>
  viewMode.value === 'week' ? addDays(startOfWeek(anchor.value), 6) : monthGrid(anchor.value)[41],
)

/** 老师筛选后端没有参数（GET /lessons 只认 class_id/from/to），只能在拉回来的窗口内过滤 */
const visibleLessons = computed(() =>
  teacherId.value ? lessons.value.filter((item) => item.teacher_id === teacherId.value) : lessons.value,
)

async function loadWindow(): Promise<void> {
  loading.value = true
  try {
    lessons.value = await listAllLessons({
      class_id: classId.value || undefined,
      teacher_id: teacherId.value || undefined,
      from: windowFrom.value,
      to: windowTo.value,
    })
    const key = `${viewMode.value}|${windowFrom.value}|${classId.value}|${teacherId.value}`
    if (viewMode.value === 'week' && key !== lastWindowKey) {
      lastWindowKey = key
      const starts = lessons.value
        .filter((item) => item.status !== 'cancelled')
        .map((item) => minutesOfDay(item.start_at))
      await scrollToMinute(starts.length > 0 ? Math.min(...starts) : 8 * 60)
    }
  } catch (error) {
    lessons.value = []
    ElMessage.error(error instanceof ApiError ? error.message : '课表加载失败')
  } finally {
    loading.value = false
  }
}

/** 切周/换筛选后把视口对到第一节课，别让人自己在 840px 的网格里找 */
const bodyRef = ref<HTMLElement | null>(null)
let lastWindowKey = ''

async function scrollToMinute(minute: number): Promise<void> {
  await nextTick()
  const element = bodyRef.value
  if (!element) return
  element.scrollTop = Math.max(0, minute - baseMin.value - 60)
}

async function loadOptions(): Promise<void> {
  try {
    const [classData, classroomData, teacherData] = await Promise.all([
      listClasses({ page: 1, page_size: 100 }),
      listClassrooms({ page: 1, page_size: 100 }),
      listTeachers({ status: 'active', page: 1, page_size: 100 }),
    ])
    classes.value = classData.items
    classrooms.value = classroomData.items
    teachers.value = teacherData.items
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '下拉数据加载失败')
  }
}

watch([viewMode, anchor, classId, teacherId], () => {
  void loadWindow()
})

// ---------------------------------------------------------------- 导航

const rangeTitle = computed(() => {
  if (viewMode.value === 'month') {
    return `${anchor.value.slice(0, 4)} 年 ${Number(anchor.value.slice(5, 7))} 月`
  }
  const days = weekDates(anchor.value)
  return `${days[0].slice(0, 4)} 年 ${formatMonthDay(days[0])} – ${formatMonthDay(days[6])}`
})

function shiftWeek(delta: number): void {
  anchor.value = addDays(anchor.value, delta * 7)
}

function shiftMonth(delta: number): void {
  const year = Number(anchor.value.slice(0, 4))
  const month = Number(anchor.value.slice(5, 7)) + delta
  const normalizedYear = year + Math.floor((month - 1) / 12)
  const normalizedMonth = ((month - 1) % 12 + 12) % 12 + 1
  const day = Math.min(Number(anchor.value.slice(8, 10)), 28)
  anchor.value = `${normalizedYear}-${String(normalizedMonth).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

function goPrev(): void {
  if (viewMode.value === 'week') shiftWeek(-1)
  else shiftMonth(-1)
}

function goNext(): void {
  if (viewMode.value === 'week') shiftWeek(1)
  else shiftMonth(1)
}

function goToday(): void {
  anchor.value = today
}

// ---------------------------------------------------------------- 周视图布局

const PX_PER_MINUTE = 1

const days = computed(() => weekDates(anchor.value))

const baseMin = computed(() => {
  const starts = visibleLessons.value.map((item) => minutesOfDay(item.start_at))
  const earliest = starts.length > 0 ? Math.min(...starts) : 8 * 60
  return Math.max(0, Math.min(8 * 60, Math.floor(earliest / 60) * 60))
})

const endMin = computed(() => {
  const ends = visibleLessons.value.map((item) => {
    const minutes = minutesOfDay(item.end_at)
    return minutes === 0 ? 24 * 60 : minutes
  })
  const latest = ends.length > 0 ? Math.max(...ends) : 22 * 60
  return Math.min(24 * 60, Math.max(22 * 60, Math.ceil(latest / 60) * 60))
})

const totalHeight = computed(() => (endMin.value - baseMin.value) * PX_PER_MINUTE)

const hourMarks = computed(() => {
  const marks: { minute: number; label: string }[] = []
  for (let minute = baseMin.value; minute <= endMin.value; minute += 60) {
    marks.push({ minute, label: clockOfMinutes(minute) })
  }
  return marks
})

const blocksByDay = computed(() => {
  const map = new Map<string, Lesson[]>()
  for (const day of days.value) map.set(day, [])
  for (const lesson of visibleLessons.value) {
    const bucket = map.get(datePart(lesson.start_at))
    if (bucket) bucket.push(lesson)
  }
  for (const bucket of map.values()) bucket.sort((a, b) => a.start_at.localeCompare(b.start_at))
  return map
})

function topOf(lesson: Lesson): number {
  return (minutesOfDay(lesson.start_at) - baseMin.value) * PX_PER_MINUTE
}

function heightOf(lesson: Lesson): number {
  const duration = durationMinutes(lesson.start_at, lesson.end_at)
  return Math.max(22, duration * PX_PER_MINUTE)
}

function blockStyle(lesson: Lesson): Record<string, string> {
  const height = heightOf(lesson)
  return {
    top: `${Math.max(0, topOf(lesson))}px`,
    height: `${height}px`,
  }
}

function isCompact(lesson: Lesson): boolean {
  return heightOf(lesson) < 46
}

// ---------------------------------------------------------------- 月视图

const monthCells = computed(() => {
  const counts = new Map<string, number>()
  for (const lesson of visibleLessons.value) {
    if (lesson.status === 'cancelled') continue
    const day = datePart(lesson.start_at)
    counts.set(day, (counts.get(day) ?? 0) + 1)
  }
  const month = anchor.value.slice(0, 7)
  return monthGrid(anchor.value).map((date) => ({
    date,
    count: counts.get(date) ?? 0,
    outside: date.slice(0, 7) !== month,
    isToday: date === today,
  }))
})

function openDay(date: string): void {
  anchor.value = date
  viewMode.value = 'week'
}

// ---------------------------------------------------------------- 拖拽

const dragState = reactive({ id: '', day: '', minute: -1, duration: 0 })
const pendingId = ref('')

function isStarted(lesson: Lesson): boolean {
  const day = datePart(lesson.start_at)
  if (day < today) return true
  if (day > today) return false
  const now = businessNowMinutes(day)
  return now !== null && minutesOfDay(lesson.start_at) <= now
}

/**
 * 不可拖：已取消 / 已开始（后端 _ensure_adjustable 会 422 LESSON_ALREADY_DONE）。
 * 「已有考勤记录」后端不通过 GET /lessons 暴露，只能靠 PATCH 的 422 兜底。
 */
function isDraggable(lesson: Lesson): boolean {
  return lesson.status !== 'cancelled' && !isStarted(lesson) && pendingId.value !== lesson.id
}

function dragTip(lesson: Lesson): string {
  if (lesson.status === 'cancelled') return '已取消的课次不可拖动'
  if (isStarted(lesson)) return '已开始或已签到的课次不可调整'
  return '拖到别的时段调整课次'
}

function onDragStart(event: DragEvent, lesson: Lesson): void {
  if (!isDraggable(lesson)) {
    event.preventDefault()
    return
  }
  dragState.id = lesson.id
  dragState.day = datePart(lesson.start_at)
  dragState.minute = minutesOfDay(lesson.start_at)
  dragState.duration = durationMinutes(lesson.start_at, lesson.end_at)
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', lesson.id)
  }
}

function onDragEnd(): void {
  dragState.id = ''
  dragState.day = ''
  dragState.minute = -1
}

function dropMinute(event: DragEvent): number {
  const element = event.currentTarget as HTMLElement
  const rect = element.getBoundingClientRect()
  const raw = baseMin.value + (event.clientY - rect.top) / PX_PER_MINUTE
  const maxStart = 24 * 60 - dragState.duration
  return Math.max(0, Math.min(snapMinutes(raw), maxStart))
}

function onDragOver(event: DragEvent, day: string): void {
  if (!dragState.id) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  dragState.day = day
  dragState.minute = dropMinute(event)
}

async function onDrop(event: DragEvent, day: string): Promise<void> {
  if (!dragState.id) return
  event.preventDefault()
  const lesson = lessons.value.find((item) => item.id === dragState.id)
  const minute = dropMinute(event)
  onDragEnd()
  if (!lesson) return
  await moveLesson(lesson, day, minute)
}

function overlayStyle(): Record<string, string> {
  return {
    top: `${(dragState.minute - baseMin.value) * PX_PER_MINUTE}px`,
    height: `${Math.max(22, dragState.duration * PX_PER_MINUTE)}px`,
  }
}

// ---------------------------------------------------------------- 冲突

const conflictVisible = ref(false)
const conflictTitle = ref('时段冲突，未保存')
const conflictRows = ref<ConflictRow[]>([])

const TYPE_ORDER: Record<ConflictType, number> = { teacher: 0, classroom: 1, student: 2 }

function sortConflicts(rows: ConflictRow[]): ConflictRow[] {
  return [...rows].sort(
    (a, b) => TYPE_ORDER[a.type] - TYPE_ORDER[b.type] || a.start_at.localeCompare(b.start_at),
  )
}

/**
 * 同一窗口内的本地预检：老师 + 教室两个维度。
 *
 * 学员维度本地算不出来——要 class_enrollment 才知跨班撞人，而 GET /lessons 不返回名单；
 * 后端 3.3 的 student 维度也只查「其他班」，同班重叠本就由老师/教室维度覆盖。跨班撞学生
 * 交给 PATCH 的 409 LESSON_OVERLAP 兜底，明细同样弹窗。口径与 find_conflicts 保持一致。
 */
function localConflicts(target: Lesson, startIso: string, endIso: string): ConflictRow[] {
  const start = Date.parse(startIso)
  const end = Date.parse(endIso)
  const rows: ConflictRow[] = []
  for (const other of lessons.value) {
    if (other.id === target.id || other.status === 'cancelled') continue
    const otherStart = Date.parse(other.start_at)
    const otherEnd = Date.parse(other.end_at)
    if (!(otherStart < end && otherEnd > start)) continue
    const dims: ConflictType[] = []
    if (other.teacher_id === target.teacher_id) dims.push('teacher')
    if (other.classroom_id === target.classroom_id) dims.push('classroom')
    for (const type of dims) {
      rows.push({
        type,
        lesson_id: other.id,
        class_id: other.class_id,
        start_at: other.start_at,
        end_at: other.end_at,
        class_name: other.class_name,
        classroom_name: other.classroom_name,
        teacher_name: other.teacher_name,
      })
    }
  }
  return sortConflicts(rows)
}

function isConflictItem(value: unknown): value is ConflictItem {
  if (typeof value !== 'object' || value === null) return false
  const item = value as Record<string, unknown>
  return typeof item.type === 'string' && typeof item.lesson_id === 'string'
}

/** 后端明细原样保留，只按 lesson_id 补上已加载课次的名字 */
function normalizeConflicts(raw: unknown[]): ConflictRow[] {
  return sortConflicts(
    raw.filter(isConflictItem).map((item) => {
      const match = lessons.value.find((lesson) => lesson.id === item.lesson_id)
      return {
        ...item,
        class_name: match?.class_name,
        classroom_name: match?.classroom_name,
        teacher_name: match?.teacher_name,
      }
    }),
  )
}

function showConflicts(rows: ConflictRow[], title: string): void {
  conflictRows.value = rows
  conflictTitle.value = title
  conflictVisible.value = true
}

function handleScheduleError(error: unknown, title: string): void {
  if (!(error instanceof ApiError)) {
    ElMessage.error('操作失败，请稍后重试')
    return
  }
  if (error.code === 'LESSON_OVERLAP' || error.code === 'SCHEDULE_CONFLICT') {
    const rows = normalizeConflicts(error.conflicts())
    if (rows.length > 0) {
      showConflicts(rows, title)
      return
    }
  }
  if (error.code === 'LESSON_ALREADY_DONE') {
    ElMessage.warning('已签到或已开始的课次不能再调整')
    return
  }
  ElMessage.error(error.message)
}

// ---------------------------------------------------------------- 调课（拖拽）

async function moveLesson(lesson: Lesson, day: string, startMinute: number): Promise<void> {
  const duration = durationMinutes(lesson.start_at, lesson.end_at)
  const startIso = isoAt(day, startMinute)
  const endIso = isoAt(day, startMinute + duration)
  if (startIso === lesson.start_at && endIso === lesson.end_at) return

  const precheck = localConflicts(lesson, startIso, endIso)
  if (precheck.length > 0) {
    // 没动过数据，块自然停在原位
    showConflicts(precheck, '拖拽落点冲突，课次已回原位')
    return
  }

  const snapshot = { start_at: lesson.start_at, end_at: lesson.end_at }
  lesson.start_at = startIso
  lesson.end_at = endIso
  pendingId.value = lesson.id
  try {
    const updated = await patchLesson(lesson.id, { start_at: startIso, end_at: endIso }, true)
    Object.assign(lesson, updated)
    ElMessage.success(`已调到 ${formatMonthDay(day)} ${timePart(startIso)}`)
  } catch (error) {
    lesson.start_at = snapshot.start_at
    lesson.end_at = snapshot.end_at
    handleScheduleError(error, '调课冲突，课次已回原位')
  } finally {
    pendingId.value = ''
  }
}

// ---------------------------------------------------------------- 详情 / 新增 / 取消

const detailVisible = ref(false)
const detailLesson = ref<Lesson | null>(null)

function openDetail(lesson: Lesson): void {
  detailLesson.value = lesson
  detailVisible.value = true
}

const editorVisible = ref(false)
const editorMode = ref<'create' | 'move'>('create')
const editorLesson = ref<Lesson | null>(null)
const submitting = ref(false)

function openCreate(): void {
  if (classes.value.length === 0) {
    ElMessage.warning('还没有班级，请先到「班级管理」建班')
    return
  }
  editorMode.value = 'create'
  editorLesson.value = null
  editorVisible.value = true
}

function openMove(lesson: Lesson): void {
  if (isStarted(lesson)) {
    ElMessage.warning('已开始或已签到的课次不能再调整')
    return
  }
  detailVisible.value = false
  editorMode.value = 'move'
  editorLesson.value = lesson
  editorVisible.value = true
}

async function handleEditorSubmit(payload: {
  class_id?: string
  classroom_id: string
  teacher_id: string
  start_at: string
  end_at: string
}): Promise<void> {
  submitting.value = true
  try {
    if (editorMode.value === 'create') {
      await createLesson(
        {
          class_id: payload.class_id ?? '',
          classroom_id: payload.classroom_id,
          teacher_id: payload.teacher_id,
          start_at: payload.start_at,
          end_at: payload.end_at,
        },
        true,
      )
      ElMessage.success('已新增课次')
    } else if (editorLesson.value) {
      const updated = await patchLesson(
        editorLesson.value.id,
        {
          start_at: payload.start_at,
          end_at: payload.end_at,
          classroom_id: payload.classroom_id,
          teacher_id: payload.teacher_id,
        },
        true,
      )
      Object.assign(editorLesson.value, updated)
      ElMessage.success('已保存调课')
    }
    editorVisible.value = false
    await loadWindow()
    if (editorMode.value === 'create') {
      // 新建的课次可能在视口外（比如晚上 18:00），落库后把网格滚过去
      await scrollToMinute(minutesOfDay(payload.start_at))
    }
  } catch (error) {
    handleScheduleError(error, editorMode.value === 'create' ? '新课时段冲突' : '调课冲突')
  } finally {
    submitting.value = false
  }
}

async function cancelLesson(lesson: Lesson): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `取消后该时段释放，可以重新排课；课次记录保留不删除。确认取消「${lesson.class_name}」${formatMonthDay(datePart(lesson.start_at))} ${timePart(lesson.start_at)} 这节课？`,
      '取消课次',
      { confirmButtonText: '确认取消', cancelButtonText: '再想想', type: 'warning' },
    )
  } catch {
    return
  }
  try {
    const updated = await patchLesson(lesson.id, { status: 'cancelled' }, true)
    Object.assign(lesson, updated)
    detailVisible.value = false
    ElMessage.success('已取消，时段已释放')
  } catch (error) {
    handleScheduleError(error, '取消失败')
  }
}

function canOperate(lesson: Lesson | null): boolean {
  return !!lesson && lesson.status !== 'cancelled' && !isStarted(lesson)
}

onMounted(() => {
  void loadOptions()
  void loadWindow()
})
</script>

<template>
  <div class="timetable">
    <el-card shadow="never" class="timetable__card">
      <div class="timetable__toolbar">
        <div class="timetable__nav">
          <el-radio-group v-model="viewMode" size="default">
            <el-radio-button value="week">周</el-radio-button>
            <el-radio-button value="month">月</el-radio-button>
          </el-radio-group>
          <el-button-group>
            <el-button @click="goPrev">上一{{ viewMode === 'week' ? '周' : '月' }}</el-button>
            <el-button @click="goToday">今天</el-button>
            <el-button @click="goNext">下一{{ viewMode === 'week' ? '周' : '月' }}</el-button>
          </el-button-group>
          <span class="timetable__range">{{ rangeTitle }}</span>
        </div>

        <div class="timetable__filters">
          <el-select
            v-model="classId"
            placeholder="全部班级"
            clearable
            filterable
            style="width: 180px"
          >
            <el-option v-for="item in classes" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
          <el-select
            v-model="teacherId"
            placeholder="全部老师"
            clearable
            filterable
            style="width: 150px"
          >
            <el-option v-for="item in teachers" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
          <el-button :loading="loading" @click="loadWindow">刷新</el-button>
          <el-button type="primary" @click="openCreate">新增单次课</el-button>
        </div>
      </div>

      <!-- 周视图：自绘网格，7 列 × 时段，1px = 1 分钟 -->
      <div v-if="viewMode === 'week'" v-loading="loading" class="timetable__week">
        <div class="timetable__header">
          <div class="timetable__gutter-head">时间</div>
          <div
            v-for="(day, index) in days"
            :key="day"
            class="timetable__day-head"
            :class="{ 'is-today': day === today }"
          >
            <span class="timetable__day-name">{{ WEEKDAY_LABELS[index] }}</span>
            <span class="timetable__day-date">{{ formatMonthDay(day) }}</span>
          </div>
        </div>

        <div
          ref="bodyRef"
          class="timetable__body"
          :style="{ gridTemplateRows: `${totalHeight}px` }"
        >
          <div class="timetable__gutter">
            <div
              v-for="mark in hourMarks"
              :key="mark.minute"
              class="timetable__hour"
              :style="{ top: `${(mark.minute - baseMin) * 1}px` }"
            >
              {{ mark.label }}
            </div>
          </div>

          <div
            v-for="day in days"
            :key="day"
            class="timetable__day"
            :class="{
              'is-over': dragState.id !== '' && dragState.day === day,
              'is-today': day === today,
            }"
            @dragover="onDragOver($event, day)"
            @drop="onDrop($event, day)"
          >
            <div
              v-if="dragState.id && dragState.day === day"
              class="timetable__ghost"
              :style="overlayStyle()"
            >
              {{ clockOfMinutes(dragState.minute) }}
            </div>

            <div
              v-for="lesson in blocksByDay.get(day) ?? []"
              :key="lesson.id"
              class="timetable__block"
              :class="[
                `is-${lesson.status}`,
                {
                  'is-compact': isCompact(lesson),
                  'is-pending': pendingId === lesson.id,
                  'is-draggable': isDraggable(lesson),
                  'is-locked': !isDraggable(lesson),
                },
              ]"
              :style="blockStyle(lesson)"
              :draggable="isDraggable(lesson)"
              :title="dragTip(lesson)"
              :data-lesson-id="lesson.id"
              :data-status="lesson.status"
              @dragstart="onDragStart($event, lesson)"
              @dragend="onDragEnd"
              @click="openDetail(lesson)"
            >
              <div class="timetable__block-time">
                {{ timePart(lesson.start_at) }}–{{ timePart(lesson.end_at) }}
                <el-tag v-if="lesson.status === 'moved'" size="small" type="warning" effect="dark">
                  已调
                </el-tag>
                <el-tag v-else-if="lesson.status === 'cancelled'" size="small" type="info" effect="plain">
                  已取消
                </el-tag>
              </div>
              <div class="timetable__block-title">{{ lesson.class_name }}</div>
              <div v-if="!isCompact(lesson)" class="timetable__block-meta">
                {{ lesson.teacher_name }} · {{ lesson.classroom_name }}
              </div>
            </div>
          </div>
        </div>

        <div v-if="visibleLessons.length === 0 && !loading" class="timetable__empty">
          这一{{ viewMode === 'week' ? '周' : '区间' }}还没有课次
        </div>
      </div>

      <!-- 月视图：只做概览，每天给「N 节课」摘要，点击跳当天周视图 -->
      <div v-else v-loading="loading" class="timetable__month">
        <div class="timetable__month-head">
          <div v-for="label in WEEKDAY_LABELS" :key="label" class="timetable__month-cell is-head">
            {{ label }}
          </div>
        </div>
        <div class="timetable__month-body">
          <button
            v-for="cell in monthCells"
            :key="cell.date"
            type="button"
            class="timetable__month-cell"
            :class="{
              'is-outside': cell.outside,
              'is-today': cell.isToday,
              'has-lesson': cell.count > 0,
            }"
            @click="openDay(cell.date)"
          >
            <span class="timetable__month-date">{{ Number(cell.date.slice(8, 10)) }}</span>
            <span v-if="cell.count > 0" class="timetable__month-count">{{ cell.count }} 节课</span>
            <span v-else class="timetable__month-count is-empty">—</span>
          </button>
        </div>
      </div>

      <div class="timetable__legend">
        <span class="timetable__dot is-normal" /> 正常
        <span class="timetable__dot is-moved" /> 已调
        <span class="timetable__dot is-cancelled" /> 已取消（不可拖）
        <span class="timetable__legend-tip">拖拽课次块到别的时段即可调课；已开始的课次不能拖。</span>
      </div>
    </el-card>

    <ConflictDialog v-model="conflictVisible" :conflicts="conflictRows" :title="conflictTitle" />

    <LessonEditorDialog
      v-model="editorVisible"
      :mode="editorMode"
      :lesson="editorLesson"
      :classes="classes"
      :classrooms="classrooms"
      :teachers="teachers"
      :submitting="submitting"
      @submit="handleEditorSubmit"
    />

    <el-dialog v-model="detailVisible" title="课次详情" width="440px">
      <el-descriptions v-if="detailLesson" :column="1" border size="small">
        <el-descriptions-item label="班级">{{ detailLesson.class_name }}</el-descriptions-item>
        <el-descriptions-item label="老师">{{ detailLesson.teacher_name }}</el-descriptions-item>
        <el-descriptions-item label="教室">{{ detailLesson.classroom_name }}</el-descriptions-item>
        <el-descriptions-item label="日期">
          {{ datePart(detailLesson.start_at) }}
        </el-descriptions-item>
        <el-descriptions-item label="时间">
          {{ timePart(detailLesson.start_at) }}–{{ timePart(detailLesson.end_at) }}
        </el-descriptions-item>
        <el-descriptions-item label="状态">
          {{ LESSON_STATUS_LABELS[detailLesson.status] }}
        </el-descriptions-item>
      </el-descriptions>

      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
        <el-button
          type="warning"
          plain
          :disabled="!canOperate(detailLesson)"
          @click="detailLesson && openMove(detailLesson)"
        >
          调课
        </el-button>
        <el-button
          type="danger"
          plain
          :disabled="!canOperate(detailLesson)"
          @click="detailLesson && cancelLesson(detailLesson)"
        >
          取消课次
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.timetable__card {
  border-radius: 10px;
}

.timetable__toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.timetable__nav,
.timetable__filters {
  display: flex;
  align-items: center;
  gap: 10px;
}

.timetable__range {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

/* ---------------- 周视图 */

.timetable__week {
  position: relative;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  overflow: hidden;
}

.timetable__header {
  display: grid;
  grid-template-columns: 64px repeat(7, minmax(0, 1fr));
  background-color: #fafafa;
  border-bottom: 1px solid #ebeef5;
}

.timetable__gutter-head,
.timetable__day-head {
  padding: 8px 6px;
  text-align: center;
  font-size: 13px;
  color: #606266;
}

.timetable__day-head {
  border-left: 1px solid #ebeef5;
}

.timetable__day-head.is-today {
  background-color: #ecf5ff;
  color: #409eff;
  font-weight: 600;
}

.timetable__day-name {
  margin-right: 4px;
}

.timetable__day-date {
  font-size: 12px;
  color: #909399;
}

.timetable__body {
  display: grid;
  grid-template-columns: 64px repeat(7, minmax(0, 1fr));
  overflow-y: auto;
  max-height: 620px;
}

.timetable__gutter {
  position: relative;
  background-color: #fafafa;
}

.timetable__hour {
  position: absolute;
  right: 6px;
  transform: translateY(-50%);
  font-size: 12px;
  color: #909399;
}

.timetable__hour:first-child {
  transform: none;
}

.timetable__day {
  position: relative;
  border-left: 1px solid #ebeef5;
  background-image: repeating-linear-gradient(
    to bottom,
    #f0f2f5 0,
    #f0f2f5 1px,
    transparent 1px,
    transparent 60px
  );
}

.timetable__day.is-today {
  background-color: #f7fbff;
}

.timetable__day.is-over {
  background-color: #ecf5ff;
  outline: 2px dashed #409eff;
  outline-offset: -2px;
}

.timetable__block {
  position: absolute;
  left: 4px;
  right: 4px;
  box-sizing: border-box;
  padding: 4px 6px;
  border-radius: 6px;
  border-left: 3px solid #409eff;
  background-color: #e8f3ff;
  color: #1f2d3d;
  font-size: 12px;
  line-height: 1.35;
  overflow: hidden;
  cursor: pointer;
  user-select: none;
  transition: box-shadow 0.15s ease;
}

.timetable__block:hover {
  box-shadow: 0 2px 10px rgb(64 158 255 / 30%);
}

.timetable__block.is-draggable {
  cursor: grab;
}

.timetable__block.is-draggable:active {
  cursor: grabbing;
}

.timetable__block.is-moved {
  border-left-color: #e6a23c;
  background-color: #fdf6ec;
}

.timetable__block.is-cancelled {
  border-left-color: #c0c4cc;
  background-color: #f4f4f5;
  color: #909399;
  text-decoration: line-through;
}

.timetable__block.is-locked {
  cursor: not-allowed;
}

.timetable__block.is-pending {
  opacity: 0.6;
  animation: tt-pulse 0.9s ease-in-out infinite;
}

.timetable__block-time {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #606266;
}

.timetable__block-title {
  margin-top: 1px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.timetable__block-meta {
  margin-top: 1px;
  font-size: 11px;
  color: #606266;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.timetable__block.is-compact {
  padding: 2px 6px;
}

.timetable__ghost {
  position: absolute;
  left: 4px;
  right: 4px;
  border: 1px dashed #409eff;
  border-radius: 6px;
  background-color: rgb(64 158 255 / 12%);
  color: #409eff;
  font-size: 11px;
  padding: 2px 6px;
  pointer-events: none;
  z-index: 2;
}

.timetable__empty {
  padding: 12px;
  text-align: center;
  font-size: 13px;
  color: #909399;
  border-top: 1px solid #ebeef5;
}

/* ---------------- 月视图 */

.timetable__month {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  overflow: hidden;
}

.timetable__month-head,
.timetable__month-body {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
}

.timetable__month-head {
  background-color: #fafafa;
  border-bottom: 1px solid #ebeef5;
}

.timetable__month-cell {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 88px;
  padding: 8px;
  border: none;
  border-left: 1px solid #ebeef5;
  border-bottom: 1px solid #ebeef5;
  background-color: #ffffff;
  text-align: left;
  font: inherit;
  cursor: pointer;
}

.timetable__month-cell:hover {
  background-color: #f5f9ff;
}

.timetable__month-cell.is-head {
  min-height: auto;
  align-items: center;
  font-size: 13px;
  color: #606266;
  cursor: default;
}

.timetable__month-cell.is-head:hover {
  background-color: transparent;
}

.timetable__month-cell.is-outside {
  background-color: #fafafa;
  color: #c0c4cc;
}

.timetable__month-cell.is-today {
  box-shadow: inset 0 0 0 2px #409eff;
}

.timetable__month-date {
  font-size: 13px;
  font-weight: 600;
}

.timetable__month-count {
  font-size: 12px;
  color: #409eff;
}

.timetable__month-count.is-empty {
  color: #dcdfe6;
}

/* ---------------- 图例 */

.timetable__legend {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  font-size: 12px;
  color: #606266;
}

.timetable__dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
}

.timetable__dot.is-normal {
  background-color: #409eff;
}

.timetable__dot.is-moved {
  background-color: #e6a23c;
}

.timetable__dot.is-cancelled {
  background-color: #c0c4cc;
}

.timetable__legend-tip {
  margin-left: 8px;
  color: #909399;
}

@keyframes tt-pulse {
  0%,
  100% {
    opacity: 0.6;
  }
  50% {
    opacity: 0.35;
  }
}
</style>
