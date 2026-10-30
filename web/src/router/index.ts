import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import type { StaffRole } from '@/types/auth'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    /** 允许进入该路由的角色；缺省表示登录即可 */
    roles?: StaffRole[]
    /** 无需登录 */
    public?: boolean
  }
}

const ALL_STAFF: StaffRole[] = ['admin', 'registrar', 'teacher']
const OFFICE: StaffRole[] = ['admin', 'registrar']

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { title: '登录', public: true },
  },
  {
    path: '/',
    component: () => import('@/layouts/AdminLayout.vue'),
    redirect: { name: 'dashboard' },
    children: [
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('@/views/DashboardView.vue'),
        meta: { title: '工作台', roles: ALL_STAFF },
      },
      {
        path: 'archive',
        redirect: { name: 'archive-students' },
      },
      {
        path: 'archive/students',
        name: 'archive-students',
        component: () => import('@/views/archive/StudentsView.vue'),
        meta: { title: '学生档案', roles: OFFICE },
      },
      {
        path: 'archive/parents',
        name: 'archive-parents',
        component: () => import('@/views/archive/ParentsView.vue'),
        meta: { title: '家长档案', roles: OFFICE },
      },
      {
        path: 'archive/teachers',
        name: 'archive-teachers',
        component: () => import('@/views/archive/TeachersView.vue'),
        meta: { title: '老师档案', roles: OFFICE },
      },
      {
        path: 'archive/classrooms',
        name: 'archive-classrooms',
        component: () => import('@/views/archive/ClassroomsView.vue'),
        meta: { title: '教室档案', roles: OFFICE },
      },
      {
        path: 'classes',
        name: 'classes',
        component: () => import('@/views/ClassesView.vue'),
        meta: { title: '班级管理', roles: OFFICE },
      },
      {
        path: 'scheduling',
        redirect: { name: 'scheduling-timetable' },
      },
      {
        path: 'scheduling/timetable',
        name: 'scheduling-timetable',
        component: () => import('@/views/scheduling/TimetableView.vue'),
        meta: { title: '排课日历', roles: OFFICE },
      },
      {
        path: 'scheduling/courses',
        name: 'scheduling-courses',
        component: () => import('@/views/scheduling/CoursesView.vue'),
        meta: { title: '课程维护', roles: OFFICE },
      },
      {
        path: 'attendance',
        name: 'attendance',
        component: () => import('@/views/PlaceholderView.vue'),
        meta: { title: '签到', roles: ALL_STAFF },
      },
      {
        path: 'reports',
        redirect: { name: 'reports-attendance' },
      },
      {
        path: 'reports/attendance',
        name: 'reports-attendance',
        component: () => import('@/views/reports/AttendanceReportView.vue'),
        meta: { title: '出勤率报表', roles: OFFICE },
      },
      {
        path: 'reports/consumption',
        name: 'reports-consumption',
        component: () => import('@/views/reports/ConsumptionReportView.vue'),
        meta: { title: '课耗报表', roles: OFFICE },
      },
      {
        path: 'reports/teacher-hours',
        name: 'reports-teacher-hours',
        component: () => import('@/views/reports/TeacherHoursReportView.vue'),
        meta: { title: '老师课时报表', roles: OFFICE },
      },
      {
        path: 'settings',
        name: 'settings',
        component: () => import('@/views/PlaceholderView.vue'),
        meta: { title: '机构设置', roles: OFFICE },
      },
      {
        path: 'my-classes',
        name: 'my-classes',
        component: () => import('@/views/PlaceholderView.vue'),
        meta: { title: '我的班级', roles: ['teacher'] },
      },
      {
        path: 'my-schedule',
        name: 'my-schedule',
        component: () => import('@/views/PlaceholderView.vue'),
        meta: { title: '我的课表', roles: ['teacher'] },
      },
      {
        path: '403',
        name: 'forbidden',
        component: () => import('@/views/ForbiddenView.vue'),
        meta: { title: '无权访问' },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { title: '页面不存在', public: true },
  },
]

export const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})

router.beforeEach((to) => {
  const auth = useAuthStore()

  if (to.meta.public) {
    // 已登录再访问登录页 → 直接进工作台
    if (to.name === 'login' && auth.isAuthenticated) {
      return { name: 'dashboard' }
    }
    return true
  }

  if (!auth.isAuthenticated) {
    // 记下原目标，登录成功后由登录页回跳
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  const allowed = to.meta.roles
  if (allowed && allowed.length > 0) {
    if (auth.role === null || !allowed.includes(auth.role)) {
      // URL 直达越权路由：拦到 403，页面组件不会被渲染
      return { name: 'forbidden', query: { from: to.fullPath } }
    }
  }

  return true
})

router.afterEach((to) => {
  const title = to.meta.title
  document.title = title ? `${title} · 托管机构管理系统` : '托管机构管理系统'
})

export default router
