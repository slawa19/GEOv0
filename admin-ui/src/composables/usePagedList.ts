import { ref, watch, type Ref } from 'vue'

import { describeError } from '../api/describeError'
import { useLatestRequest } from './useLatestRequest'

export type PagedRequest = { page: number; perPage: number }
export type PagedResult<T> = { items: T[]; total: number }

/**
 * The state machinery of a server-paged list (032 S7, D-9), once instead of four copies: page, page size, total,
 * rows, `loading` and the text of the last failure, with these rules.
 *
 * - The latest request owns the state: an older answer - success or failure - and an answer that arrives after the
 *   page is gone change nothing (`useLatestRequest`).
 * - A page past the last one (the list shrank under the operator) is corrected to the last page and that page is
 *   read; the stale, empty answer is never shown.
 * - A change of page or of page size loads once. `reloadFromFirstPage` is for a change of the filters: from page 1
 *   it loads; from any other page it only moves to page 1 and lets the page watcher load - one request either way
 *   (the copies asked twice from page 2 on).
 *
 * The composable does not load by itself: a page hydrates its filters from the route first, then calls `reload()`.
 * `fetchPage` reads the page's filters when it is called, so it always asks with the current ones.
 */
export function usePagedList<T>(
  fetchPage: (request: PagedRequest) => Promise<PagedResult<T>>,
  options: { errorKey: string; perPage?: number },
) {
  const page = ref(1)
  const perPage = ref(options.perPage ?? 20)
  const total = ref(0)
  const items = ref([]) as Ref<T[]>
  const loading = ref(false)
  const error = ref<string | null>(null)
  const requests = useLatestRequest()

  async function reload() {
    const request = requests.begin()
    const requestPage = page.value
    const requestPerPage = perPage.value
    loading.value = true
    error.value = null
    try {
      const data = await fetchPage({ page: requestPage, perPage: requestPerPage })
      if (!request.isCurrent()) return
      total.value = data.total
      const maxPage = Math.max(1, Math.ceil(data.total / requestPerPage))
      if (requestPage > maxPage) {
        page.value = maxPage
        return
      }
      items.value = data.items
    } catch (e: unknown) {
      if (!request.isCurrent()) return
      error.value = describeError(e, options.errorKey).text
    } finally {
      if (request.isCurrent()) loading.value = false
    }
  }

  function reloadFromFirstPage() {
    if (page.value !== 1) page.value = 1
    else void reload()
  }

  watch(page, () => void reload())
  watch(perPage, reloadFromFirstPage)

  return { page, perPage, total, items, loading, error, reload, reloadFromFirstPage }
}
