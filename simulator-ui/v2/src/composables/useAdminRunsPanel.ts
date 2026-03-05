import type { Ref } from 'vue'
import { computed, onUnmounted, ref, watch } from 'vue'
import type { RunStatus } from '../api/simulatorTypes'
import type { AdminRunSummary } from '../api/simulatorApi'
import { adminGetAllRuns, adminStopAllRuns } from '../api/simulatorApi'
import { ApiError } from '../api/http'
import { toLower } from '../utils/stringHelpers'
import { isJwtLike } from '../utils/isJwtLike'

export function useAdminRunsPanel(deps: {
  isRealMode: Readonly<Ref<boolean>>
  apiBase: Readonly<Ref<string>>
  accessToken: Readonly<Ref<string | null | undefined>>
  runId: Readonly<Ref<string | null | undefined>>
  runStatus: Readonly<Ref<RunStatus | null | undefined>>
}): {
  runs: Ref<AdminRunSummary[] | null>
  loading: Ref<boolean>
  lastError: Ref<string>
  getRuns: () => Promise<void>
  stopRuns: () => Promise<void>
  scheduleRefresh: () => void
  dispose: () => void
} {
  const runs = ref<AdminRunSummary[] | null>(null)
  const loading = ref(false)
  const lastError = ref('')

  const hasAdminToken = computed(() => {
    const t = String(deps.accessToken.value ?? '').trim()
    return !!t && !isJwtLike(t)
  })

  let disposed = false
  let refreshTimer: number | null = null

  async function getRuns(): Promise<void> {
    if (disposed) return
    loading.value = true
    lastError.value = ''
    try {
      const res = await adminGetAllRuns({ apiBase: deps.apiBase.value, accessToken: deps.accessToken.value })
      runs.value = res.items ?? []
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 403) {
        lastError.value = 'Admin token rejected (HTTP 403)'
      } else {
        lastError.value = String((e as any)?.message ?? e)
      }
    } finally {
      loading.value = false
    }
  }

  async function stopRuns(): Promise<void> {
    if (disposed) return
    lastError.value = ''
    try {
      await adminStopAllRuns({ apiBase: deps.apiBase.value, accessToken: deps.accessToken.value })
      await getRuns()
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 403) {
        lastError.value = 'Admin token rejected (HTTP 403)'
      } else {
        lastError.value = String((e as any)?.message ?? e)
      }
    }
  }

  function scheduleRefresh() {
    if (disposed) return
    if (!deps.isRealMode.value) return
    if (!hasAdminToken.value) return
    if (loading.value) return

    if (refreshTimer != null) return
    refreshTimer = window.setTimeout(async () => {
      refreshTimer = null
      if (disposed) return
      if (!deps.isRealMode.value || !hasAdminToken.value) return
      if (loading.value) return
      await getRuns()
    }, 200)
  }

  watch(
    () => [deps.isRealMode.value, hasAdminToken.value, deps.apiBase.value] as const,
    ([isReal, hasAdmin]) => {
      if (!isReal || !hasAdmin) return
      scheduleRefresh()
    },
    { immediate: true },
  )

  watch(
    () => deps.runId.value,
    () => {
      scheduleRefresh()
    },
  )

  watch(
    () => toLower(deps.runStatus.value?.state),
    () => {
      scheduleRefresh()
    },
  )

  function dispose() {
    disposed = true
    if (refreshTimer != null) window.clearTimeout(refreshTimer)
    refreshTimer = null
  }

  onUnmounted(() => {
    dispose()
  })

  return { runs, loading, lastError, getRuns, stopRuns, scheduleRefresh, dispose }
}
