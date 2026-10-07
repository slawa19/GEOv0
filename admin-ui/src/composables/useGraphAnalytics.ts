import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'

import { api } from '../api'
import { describeError } from '../api/describeError'
import { makeMetricsKey } from '../pages/graph/graphPageHelpers'
import type { BalanceRow, ParticipantMetrics } from '../types/domain'
import type { SelectedInfo } from './useGraphVisualization'
import { normalizeEquivalentCode } from '../utils/equivalent'
import { useLatestRequest } from './useLatestRequest'

/**
 * The drawer's balance rows of the selected participant (032 S5, F-1).
 *
 * The rows come only from the server (`balance_rows` of `/participants/{pid}/metrics`). While the
 * request is in flight the drawer shows loading, on failure the error; nothing is computed from the
 * graph snapshot in their place - the snapshot is filtered by status and by the focus view, so a
 * figure derived from it would be a different quantity printed under the same heading.
 */
export function useGraphAnalytics(opts: {
  analyticsEq: ComputedRef<string | null>
  selected: Ref<SelectedInfo | null>
}) {
  const metricsCache = ref(new Map<string, ParticipantMetrics>())
  const metricsLoading = ref(false)
  const metricsError = ref<string | null>(null)
  const metricsRequests = useLatestRequest()

  const selectedPid = computed(() => (opts.selected.value && opts.selected.value.kind === 'node' ? opts.selected.value.pid : ''))
  const selectedEqCode = computed(() => {
    const eqCode = normalizeEquivalentCode(opts.analyticsEq.value || '')
    return eqCode || null
  })

  const selectedMetrics = computed(() => {
    const pid = selectedPid.value
    if (!pid) return null
    return metricsCache.value.get(makeMetricsKey(pid, selectedEqCode.value)) || null
  })

  async function loadSelectedMetrics() {
    const request = metricsRequests.begin()
    const pid = selectedPid.value
    if (!pid) {
      metricsLoading.value = false
      metricsError.value = null
      return
    }

    const eqCode = selectedEqCode.value
    const key = makeMetricsKey(pid, eqCode)
    if (metricsCache.value.has(key)) {
      metricsLoading.value = false
      metricsError.value = null
      return
    }

    metricsLoading.value = true
    metricsError.value = null
    try {
      const res = await api.participantMetrics(pid, { equivalent: eqCode })
      if (!request.isCurrent()) return
      metricsCache.value.set(key, res)
    } catch (e: unknown) {
      if (!request.isCurrent()) return
      metricsError.value = describeError(e, 'graph.analytics.metricsLoadFailed').text
    } finally {
      if (request.isCurrent()) metricsLoading.value = false
    }
  }

  watch([selectedPid, selectedEqCode], () => {
    void loadSelectedMetrics()
  })

  const selectedBalanceRows = computed<BalanceRow[]>(() => selectedMetrics.value?.balance_rows ?? [])

  return {
    metricsLoading,
    metricsError,
    loadSelectedMetrics,

    selectedBalanceRows,
  }
}
