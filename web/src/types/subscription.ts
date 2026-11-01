/** 订阅状态（7.2 /me/subscription）：前端横幅数据源 */
export interface SubscriptionStatus {
  plan_name: string
  end_date: string
  days_left: number
  expired: boolean
}
