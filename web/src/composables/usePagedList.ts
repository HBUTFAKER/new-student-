import { ref } from 'vue'
import type { Ref } from 'vue'

import type { Paged } from '@/types/archive'

type Query = Record<string, unknown>
type Fetcher<T> = (params: Query) => Promise<Paged<T>>

/**
 * 列表页的公共逻辑：分页、加载、错误、导出取全量。
 * 各页面只负责「筛选条件 + 表格列 + 表单」。
 */
export function usePagedList<T>(fetcher: Fetcher<T>, defaultPageSize = 10) {
  const items = ref([]) as Ref<T[]>
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(defaultPageSize)
  const loading = ref(false)
  const errorText = ref('')

  async function load(filters: Query = {}): Promise<void> {
    loading.value = true
    errorText.value = ''
    try {
      const data = await fetcher({ ...filters, page: page.value, page_size: pageSize.value })
      items.value = data.items
      total.value = data.total
    } catch (error) {
      errorText.value = error instanceof Error ? error.message : '加载失败'
      items.value = []
      total.value = 0
    } finally {
      loading.value = false
    }
  }

  /** 导出用：把当前筛选条件下的全部页都拉下来（page_size=100 是后端上限） */
  async function fetchAll(filters: Query = {}): Promise<T[]> {
    const collected: T[] = []
    let current = 1
    for (;;) {
      const data = await fetcher({ ...filters, page: current, page_size: 100 })
      collected.push(...data.items)
      if (data.items.length === 0 || collected.length >= data.total) break
      current += 1
    }
    return collected
  }

  return { items, total, page, pageSize, loading, errorText, load, fetchAll }
}
