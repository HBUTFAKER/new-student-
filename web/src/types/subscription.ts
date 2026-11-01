/** 订阅状态（7.2/7.3 /me/subscription）：前端横幅 + 用量提示数据源 */
export interface SubscriptionStatus {
  plan_name: string
  end_date: string
  days_left: number
  expired: boolean
  students_used: number
  students_max: number
  messages_used: number
  messages_max: number
}
