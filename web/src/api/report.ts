import { http } from './http'
import type {
  AttendanceSummary,
  ConsumptionReport,
  TeacherHoursReport,
} from '@/types/report'

/** 窗口参数必须是带时区的 ISO 串（`2026-11-01T00:00:00+08:00`），否则 422 TIMEZONE_REQUIRED */
export interface ReportWindowQuery {
  from: string
  to: string
}

/** ---- 出勤率（05 §8 一期扩容，08 §0 [4.4 上]） ---- */
export function getAttendanceSummary(
  params: ReportWindowQuery & { class_id?: string; detail?: boolean },
): Promise<AttendanceSummary> {
  // detail 只在开启时带上，避免 URL 里挂一个恒假的参数
  const query = params.detail ? params : { ...params, detail: undefined }
  return http
    .get<AttendanceSummary>('/reports/attendance-summary', { params: query })
    .then((r) => r.data)
}

/** ---- 课耗 ---- */
export function getConsumption(params: ReportWindowQuery): Promise<ConsumptionReport> {
  return http.get<ConsumptionReport>('/reports/consumption', { params }).then((r) => r.data)
}

/** ---- 老师课时 ---- */
export function getTeacherHours(params: ReportWindowQuery): Promise<TeacherHoursReport> {
  return http.get<TeacherHoursReport>('/reports/teacher-hours', { params }).then((r) => r.data)
}
