import { http } from './http'
import type { SubscriptionStatus } from '@/types/subscription'

/** 订阅状态（7.2）：登录后拉一次，驱动顶栏到期横幅 */
export function getMySubscription(): Promise<SubscriptionStatus> {
  return http.get<SubscriptionStatus>('/me/subscription').then((r) => r.data)
}
