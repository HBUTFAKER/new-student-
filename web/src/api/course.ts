import { http } from './http'
import type { Student } from '@/types/archive'
import type { ClassItem, ClassPayload, Course, CoursePayload, Enrollment } from '@/types/course'

type Query = Record<string, unknown>

interface Paged<T> {
  items: T[]
  total: number
}

/** ---- 课程（05 §3.1） ---- */
export function listCourses(params: Query = {}): Promise<Paged<Course>> {
  return http.get<Paged<Course>>('/courses', { params }).then((r) => r.data)
}

export function createCourse(payload: CoursePayload): Promise<Course> {
  return http.post<Course>('/courses', payload).then((r) => r.data)
}

export function updateCourse(id: string, payload: Partial<CoursePayload>): Promise<Course> {
  return http.patch<Course>(`/courses/${id}`, payload).then((r) => r.data)
}

/** 被班级引用时后端 409 COURSE_IN_USE —— 只能停用 */
export function deleteCourse(id: string): Promise<void> {
  return http.delete(`/courses/${id}`).then(() => undefined)
}

/** ---- 班级（05 §3.2） ---- */
export function listClasses(params: Query): Promise<Paged<ClassItem>> {
  return http.get<Paged<ClassItem>>('/classes', { params }).then((r) => r.data)
}

export function createClass(payload: ClassPayload): Promise<ClassItem> {
  return http.post<ClassItem>('/classes', payload).then((r) => r.data)
}

export function updateClass(id: string, payload: Partial<ClassPayload>): Promise<ClassItem> {
  return http.patch<ClassItem>(`/classes/${id}`, payload).then((r) => r.data)
}

/** ---- 班级学员名单（05 §3.3） ---- */
export function listClassStudents(classId: string): Promise<Paged<Enrollment>> {
  return http
    .get<Paged<Enrollment>>(`/classes/${classId}/students`)
    .then((r) => r.data)
}

export function enrollStudent(classId: string, studentId: string): Promise<Enrollment> {
  return http
    .post<Enrollment>(`/classes/${classId}/students`, { student_id: studentId })
    .then((r) => r.data)
}

export function removeStudent(classId: string, studentId: string): Promise<void> {
  return http.delete(`/classes/${classId}/students/${studentId}`).then(() => undefined)
}

/** 加学员弹窗用的在读学生候选（复用 /students 过滤） */
export function listActiveStudents(): Promise<Paged<Student>> {
  return http
    .get<Paged<Student>>('/students', { params: { status: 'active', page: 1, page_size: 100 } })
    .then((r) => r.data)
}
