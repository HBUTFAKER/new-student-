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
        name: 'archive',
        component: () => import('@/views/PlaceholderView.vue'),
        meta: { title: '档案管理', roles: OFFICE },
      },
      {
        path: 'classes',
        name: 'classes',
        component: () => import('@/views/PlaceholderView.vue'),
        meta: { title: '班级管理', roles: OFFICE },
      },
      {
        path: 'scheduling',
        name: 'scheduling',
        component: () => import('@/views/PlaceholderView.vue'),
        meta: { title: '排课管理', roles: OFFICE },
      },
      {
        path: 'attendance',
        name: 'attendance',
        component: () => import('@/views/PlaceholderView.vue'),
        meta: { title: '签到', roles: ALL_STAFF },
      },
      {
        path: 'reports',
        name: 'reports',
        component: () => import('@/views/PlaceholderView.vue'),
        meta: { title: '报表', roles: OFFICE },
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
