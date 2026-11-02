import { http } from './http'

/** 考勤结果行（后端 AttendanceRowOut）：status 含 present/leave/absent/makeup */
export interface AttendanceRow {
  student_id: string
  status: string
  deduct_lessons: number
  operator_id: string
}

export interface AttendanceList {
  items: AttendanceRow[]
  total: number
}

/** 签到结果（后端 AttendanceMarkOut） */
export interface AttendanceMarkOut {
  lesson_id: string
  results: {
    student_id: string
    status: string
    deduct_lessons: number
    deduplicated: boolean
    reason: string | null
  }[]
}

/** ---- 考勤（后端 3.5，前端 047 补课排期取缺勤名单用） ---- */

/** 课堂点名结果（含 makeup 补课座位） */
export function getAttendance(lessonId: string): Promise<AttendanceList> {
  return http.get<AttendanceList>(`/lessons/${lessonId}/attendance`).then((r) => r.data)
}

/** 课堂点名签到（同事务扣课，幂等） */
export function markAttendance(
  lessonId: string,
  records: { student_id: string; status: 'present' | 'leave' | 'absent' }[],
): Promise<AttendanceMarkOut> {
  return http
    .post<AttendanceMarkOut>(`/lessons/${lessonId}/attendance`, { records })
    .then((r) => r.data)
}
