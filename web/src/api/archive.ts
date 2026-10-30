import { http } from './http'
import type {
  Binding,
  Classroom,
  ClassroomPayload,
  InviteCode,
  InviteCodeList,
  Paged,
  Parent,
  ParentPayload,
  Student,
  StudentPayload,
  Teacher,
  TeacherPayload,
} from '@/types/archive'

type Query = Record<string, unknown>

/** ---- 学生（05 §2.1） ---- */
export function listStudents(params: Query): Promise<Paged<Student>> {
  return http.get<Paged<Student>>('/students', { params }).then((r) => r.data)
}

export function createStudent(payload: StudentPayload): Promise<Student> {
  return http.post<Student>('/students', payload).then((r) => r.data)
}

export function updateStudent(id: string, payload: Partial<StudentPayload>): Promise<Student> {
  return http.patch<Student>(`/students/${id}`, payload).then((r) => r.data)
}

/** ---- 家长（05 §2.2） ---- */
export function listParents(params: Query): Promise<Paged<Parent>> {
  return http.get<Paged<Parent>>('/parents', { params }).then((r) => r.data)
}

export function createParent(payload: ParentPayload): Promise<Parent> {
  return http.post<Parent>('/parents', payload).then((r) => r.data)
}

export function updateParent(id: string, payload: Partial<ParentPayload>): Promise<Parent> {
  return http.patch<Parent>(`/parents/${id}`, payload).then((r) => r.data)
}

/** ---- 老师（05 §2.5） ---- */
export function listTeachers(params: Query): Promise<Paged<Teacher>> {
  return http.get<Paged<Teacher>>('/teachers', { params }).then((r) => r.data)
}

export function createTeacher(payload: TeacherPayload): Promise<Teacher> {
  return http.post<Teacher>('/teachers', payload).then((r) => r.data)
}

export function updateTeacher(id: string, payload: Partial<TeacherPayload>): Promise<Teacher> {
  return http.patch<Teacher>(`/teachers/${id}`, payload).then((r) => r.data)
}

/** ---- 教室（05 §2.6） ---- */
export function listClassrooms(params: Query): Promise<Paged<Classroom>> {
  return http.get<Paged<Classroom>>('/classrooms', { params }).then((r) => r.data)
}

export function createClassroom(payload: ClassroomPayload): Promise<Classroom> {
  return http.post<Classroom>('/classrooms', payload).then((r) => r.data)
}

export function updateClassroom(
  id: string,
  payload: Partial<ClassroomPayload>,
): Promise<Classroom> {
  return http.patch<Classroom>(`/classrooms/${id}`, payload).then((r) => r.data)
}

/** ---- 家长-学生绑定（05 §2.3） ---- */
export function listBindings(params: Query): Promise<Paged<Binding>> {
  return http.get<Paged<Binding>>('/bindings', { params }).then((r) => r.data)
}

export function createBinding(payload: {
  parent_id: string
  student_id: string
  relation?: string | null
}): Promise<Binding> {
  return http.post<Binding>('/bindings', payload).then((r) => r.data)
}

export function deleteBinding(id: string): Promise<void> {
  return http.delete(`/bindings/${id}`).then(() => undefined)
}

/** ---- 邀请码（05 §2.4） ---- */
export function createInviteCode(payload: {
  student_id: string
  expires_in_hours?: number
}): Promise<InviteCode> {
  return http.post<InviteCode>('/invite-codes', payload).then((r) => r.data)
}

export function listInviteCodes(studentId: string): Promise<InviteCodeList> {
  return http.get<InviteCodeList>(`/students/${studentId}/invite-codes`).then((r) => r.data)
}
