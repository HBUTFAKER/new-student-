/**
 * 报表页的窗口换算与展示格式化。
 *
 * 两条铁律（08 §0 [4.4 上] 第 2、3 条）：
 * - 接口要的是**带 +08:00 的 ISO 串**，`from`/`to` 只是日期会 422；
 * - 窗口 `[from, to)` 左闭右开，所以「到 11/30」实际要传 `12/1T00:00:00+08:00`。
 *
 * 日期一律按字符串算，绝不 `new Date('2026-11-01')` 再取本地日期——那会随机器时区串月。
 */

import { addDays, businessToday } from './timetable'

/** 含首尾的日历区间，`YYYY-MM-DD` */
export interface DateRange {
  start: string
  end: string
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

/** 本月 1 号 ~ 今天（指令默认口径） */
export function monthToDateRange(): DateRange {
  const today = businessToday()
  return { start: `${today.slice(0, 7)}-01`, end: today }
}

/** 整月；offset=-1 是上月，0 是本月（本月未过完时截到今天） */
export function monthRange(offset = 0): DateRange {
  const today = businessToday()
  const [year, month] = today.split('-').map(Number)
  const base = new Date(Date.UTC(year, month - 1 + offset, 1))
  const targetYear = base.getUTCFullYear()
  const targetMonth = base.getUTCMonth() + 1
  const start = `${targetYear}-${pad(targetMonth)}-01`
  // 下个月的第 0 天 = 本月最后一天，闰年由 Date 自己算
  const lastDay = new Date(Date.UTC(targetYear, targetMonth, 0)).getUTCDate()
  const end = `${targetYear}-${pad(targetMonth)}-${pad(lastDay)}`
  return { start, end: offset === 0 && today < end ? today : end }
}

/** 最近 N 天（含今天） */
export function lastDaysRange(days: number): DateRange {
  const today = businessToday()
  return { start: addDays(today, -(Math.max(1, days) - 1)), end: today }
}

/** 含首尾的日历区间 → 接口要的左闭右开、带时区的 ISO 串 */
export function toWindowIso(range: DateRange): { from: string; to: string } {
  return {
    from: `${range.start}T00:00:00+08:00`,
    // end 当天要算进去，所以右界推到次日 0 点
    to: `${addDays(range.end, 1)}T00:00:00+08:00`,
  }
}

/** `from >= to` 前端先拦一道（后端也会 422 RANGE_INVALID） */
export function isRangeValid(range: DateRange): boolean {
  return Boolean(range.start) && Boolean(range.end) && range.start <= range.end
}

/** 出勤率：`null` 是「没数据」，必须显示「—」，不能落成 0% */
export function formatRate(rate: number | null): string {
  return rate === null ? '—' : `${rate}%`
}

/** 分钟 → `30h 00m` */
export function formatMinutes(minutes: number): string {
  const total = Math.max(0, Math.round(minutes))
  return `${Math.floor(total / 60)}h ${pad(total % 60)}m`
}

/** 统计窗口的人类可读说法：`2026-11-01 ~ 2026-11-30` */
export function describeRange(range: DateRange): string {
  return `${range.start} ~ ${range.end}`
}

/** 导出文件名带日期范围，避免多次导出互相覆盖 */
export function reportFileName(title: string, range: DateRange): string {
  return `${title}_${range.start}_${range.end}.csv`
}
