/**
 * 课表用的时间换算。
 *
 * 口径约定（与后端 3.1–3.4 一致）：
 * - 后端所有 datetime 都是 `+08:00` 业务时区的 ISO 串，前端显示直接截取字符，
 *   绝不走 `new Date()` 再取本地时间——否则换台机器就串日期。
 * - 星期 0=周一 … 6=周日，等于 `date.weekday()`，与 3.1 的 weekday 同口径。
 */

const DAY_MS = 24 * 60 * 60 * 1000

/** 2026-11-03T19:00:00+08:00 → 2026-11-03 */
export function datePart(iso: string): string {
  return iso.slice(0, 10)
}

/** 2026-11-03T19:00:00+08:00 → 19:00 */
export function timePart(iso: string): string {
  return iso.slice(11, 16)
}

/** 当天第几分钟 */
export function minutesOfDay(iso: string): number {
  return Number(iso.slice(11, 13)) * 60 + Number(iso.slice(14, 16))
}

/** "19:30" → 1170 */
export function minutesOfClock(clock: string): number {
  const [hour, minute] = clock.split(':')
  return Number(hour) * 60 + Number(minute)
}

/** 1170 → "19:30" */
export function clockOfMinutes(minutes: number): string {
  const safe = Math.max(0, Math.min(24 * 60 - 1, Math.round(minutes)))
  const hour = String(Math.floor(safe / 60)).padStart(2, '0')
  const minute = String(safe % 60).padStart(2, '0')
  return `${hour}:${minute}`
}

/** 日期 + 当天分钟数 → 后端要的 aware ISO 串（固定 +08:00，不用本机时区） */
export function isoAt(date: string, minutes: number): string {
  return `${date}T${clockOfMinutes(minutes)}:00+08:00`
}

/** 无时区依赖的星期推导：0=周一 */
export function weekdayOf(date: string): number {
  const day = new Date(`${date}T00:00:00Z`).getUTCDay()
  return (day + 6) % 7
}

export function addDays(date: string, days: number): string {
  const shifted = new Date(`${date}T00:00:00Z`).getTime() + days * DAY_MS
  return new Date(shifted).toISOString().slice(0, 10)
}

/** 所在周的周一 */
export function startOfWeek(date: string): string {
  return addDays(date, -weekdayOf(date))
}

/** 该周 7 天（周一 → 周日） */
export function weekDates(date: string): string[] {
  const monday = startOfWeek(date)
  return Array.from({ length: 7 }, (_, index) => addDays(monday, index))
}

/**
 * 月视图网格 42 天（周一为每行起点，含上月/下月补格）。
 * 月初所在周的周一起算，铺满 6 行。
 */
export function monthGrid(date: string): string[] {
  const first = `${date.slice(0, 7)}-01`
  const gridStart = startOfWeek(first)
  return Array.from({ length: 42 }, (_, index) => addDays(gridStart, index))
}

/** 业务时区（UTC+8）的今天，不依赖本机时区 */
export function businessToday(): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date())
}

/** 业务时区的此刻，用于判断课次是否已经开始（后端 LESSON_ALREADY_DONE 同口径） */
export function businessNowMinutes(date: string): number | null {
  if (date !== businessToday()) return null
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Shanghai',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date())
  const [hour, minute] = parts.split(':')
  return Number(hour) * 60 + Number(minute)
}

/** 课次时长（分钟）；跨零点按整天算，避免 end<start 时出现负高度 */
export function durationMinutes(startIso: string, endIso: string): number {
  const start = minutesOfDay(startIso)
  let end = minutesOfDay(endIso)
  if (datePart(endIso) !== datePart(startIso)) end += 24 * 60
  return Math.max(5, end - start)
}

/** 15 分钟吸附（拖拽落点用） */
export function snapMinutes(minutes: number, step = 15): number {
  return Math.round(minutes / step) * step
}

export function formatMonthDay(date: string): string {
  return `${Number(date.slice(5, 7))}/${Number(date.slice(8, 10))}`
}

export const WEEKDAY_LABELS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
