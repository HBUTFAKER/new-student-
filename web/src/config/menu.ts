import type { StaffRole } from '@/types/auth'

export interface MenuItem {
  title: string
  path: string
  /** @element-plus/icons-vue 的组件名，main.ts 已全局注册 */
  icon: string
  children?: MenuItem[]
}

/** 教务 / 机构管理员（0.2 矩阵：档案、班级、排课、报表、机构设置全部它们是唯一可写角色） */
const OFFICE_MENU: MenuItem[] = [
  { title: '工作台', path: '/dashboard', icon: 'Odometer' },
  {
    title: '档案管理',
    path: '/archive',
    icon: 'FolderOpened',
    children: [
      { title: '学生档案', path: '/archive/students', icon: 'User' },
      { title: '家长档案', path: '/archive/parents', icon: 'Avatar' },
      { title: '老师档案', path: '/archive/teachers', icon: 'Reading' },
      { title: '教室档案', path: '/archive/classrooms', icon: 'OfficeBuilding' },
    ],
  },
  { title: '班级管理', path: '/classes', icon: 'Collection' },
  { title: '排课管理', path: '/scheduling', icon: 'Calendar' },
  { title: '签到', path: '/attendance', icon: 'Checked' },
  { title: '报表', path: '/reports', icon: 'DataLine' },
  { title: '机构设置', path: '/settings', icon: 'Setting' },
]

/** 老师：只看自己任教的班级与课表，签到收窄到任教课次 */
const TEACHER_MENU: MenuItem[] = [
  { title: '工作台', path: '/dashboard', icon: 'Odometer' },
  { title: '我的班级', path: '/my-classes', icon: 'Collection' },
  { title: '我的课表', path: '/my-schedule', icon: 'Calendar' },
  { title: '签到', path: '/attendance', icon: 'Checked' },
]

export function menuForRole(role: StaffRole | null): MenuItem[] {
  if (role === null) return []
  return role === 'teacher' ? TEACHER_MENU : OFFICE_MENU
}
