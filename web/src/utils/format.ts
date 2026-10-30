/** 表格里的时间格式化（后端返回 ISO 8601 带时区） */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 日期（YYYY-MM-DD 原样返回，避免时区把日期减一天） */
export function formatDate(value: string | null | undefined): string {
  return value ? value.slice(0, 10) : '-'
}
