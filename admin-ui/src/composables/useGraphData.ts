import { computed, ref, watch, type Ref } from 'vue'
import { ElMessage } from 'element-plus'

import { api } from '../api'
import { describeError } from '../api/describeError'
import { buildFocusModeQuery } from '../pages/graph/graphPageHelpers'
import { normalizeEquivalentCode } from '../utils/equivalent'
import { buildPrecisionByEquivalent } from './useEquivalentPrecision'
import { useLatestRequest } from './useLatestRequest'
import type { Equivalent, GraphSnapshotPayload, Participant, Trustline } from '../pages/graph/graphTypes'

// 032 S5 (F-1). The page asks for no optional snapshot collection: `transactions` was requested
// only for the drawer's activity card, `incidents` fed the incident overlay, and both are removed
// together with the participant analytics. Balance rows come from `/participants/{pid}/metrics`.

/**
 * Compute the primary (most popular) equivalent based on active trustlines count.
 * Returns the equivalent code with the most trustlines, or first available, or empty string.
 */
export function computePrimaryEquivalent(trustlines: { equivalent: string; status?: string }[], equivalents: { code: string }[]): string {
  const countByEq = new Map<string, number>()
  for (const t of trustlines || []) {
    const status = String(t.status || '').toLowerCase()
    if (status !== 'active') continue
    const code = normalizeEquivalentCode(t.equivalent)
    if (!code) continue
    countByEq.set(code, (countByEq.get(code) || 0) + 1)
  }

  let maxCode = ''
  let maxCount = 0
  for (const [code, count] of countByEq) {
    if (count > maxCount) {
      maxCode = code
      maxCount = count
    }
  }

  if (maxCode) return maxCode

  // Fallback: first equivalent from the list
  const first = (equivalents || [])[0]
  return first ? normalizeEquivalentCode(first.code) : ''
}

export function filterTrustlinesByEqAndStatus(input: {
  trustlines: Trustline[]
  equivalent: string
  statusFilter: string[]
}): Trustline[] {
  const eqKey = normalizeEquivalentCode(input.equivalent)
  const allowed = new Set((input.statusFilter || []).map((s) => String(s).toLowerCase()).filter(Boolean))

  return (input.trustlines || []).filter((t) => {
    // Filter by equivalent (empty = show all)
    if (eqKey && normalizeEquivalentCode(t.equivalent) !== eqKey) return false
    if (allowed.size && !allowed.has(String(t.status || '').toLowerCase())) return false
    return true
  })
}

export function useGraphData(opts: {
  eq: Ref<string>
  focusMode: Ref<boolean>
  focusRootPid: Ref<string>
  focusDepth: Ref<number>
  statusFilter: Ref<string[]>
}) {
  const loading = ref(false)
  const error = ref<string | null>(null)

  const participants = ref<Participant[]>([])
  const trustlines = ref<Trustline[]>([])
  const equivalents = ref<Equivalent[]>([])

  // 028 F-028-49 (C2, owner В-3): an equivalent the page chose for the operator is SHOWN as chosen, until the
  // operator picks one; one graph shows one equivalent, and the operator must see which and why.
  const autoSelectedEq = ref('')
  const eqAutoSelected = computed(() => !!autoSelectedEq.value && normalizeEquivalentCode(opts.eq.value) === autoSelectedEq.value)
  watch(opts.eq, (v) => {
    if (normalizeEquivalentCode(v) !== autoSelectedEq.value) autoSelectedEq.value = ''
  })

  const availableEquivalents = computed(() => {
    const fromDs = (equivalents.value || []).map((e) => normalizeEquivalentCode(e.code)).filter(Boolean)
    const fromTls = (trustlines.value || []).map((t) => normalizeEquivalentCode(t.equivalent)).filter(Boolean)
    // Note: 'ALL' option removed — now we always select a specific equivalent for proper viz_* support
    return Array.from(new Set([...fromDs, ...fromTls])).sort()
  })

  // The one precision map (`buildPrecisionByEquivalent`): the same predicate for every consumer.
  const precisionByEq = computed(() => buildPrecisionByEquivalent(equivalents.value))

  const participantByPid = computed(() => {
    const m = new Map<string, Participant>()
    for (const p of participants.value || []) {
      if (p?.pid) m.set(p.pid, p)
    }
    return m
  })

  const filteredTrustlines = computed(() => {
    return filterTrustlinesByEqAndStatus({
      trustlines: trustlines.value || [],
      equivalent: opts.eq.value,
      statusFilter: opts.statusFilter.value || [],
    })
  })

  let fullSnapshot: GraphSnapshotPayload | null = null
  const viewRequests = useLatestRequest()

  function invalidateDataOwnership() {
    viewRequests.invalidate()
    loading.value = false
  }

  function toPayload(src: Partial<GraphSnapshotPayload>): GraphSnapshotPayload {
    return {
      participants: (src.participants || []) as Participant[],
      trustlines: (src.trustlines || []) as Trustline[],
      equivalents: (src.equivalents || []) as Equivalent[],
    }
  }

  function applySnapshotPayload(p: GraphSnapshotPayload) {
    participants.value = p.participants || []
    trustlines.value = p.trustlines || []
    equivalents.value = p.equivalents || []
  }

  async function loadData(): Promise<boolean> {
    const viewRequest = viewRequests.begin()
    loading.value = true
    error.value = null
    try {
      // First load without equivalent to get full trustlines list for primary equivalent computation
      const snapEq = normalizeEquivalentCode(opts.eq.value)
      const snap = await api.graphSnapshot({ equivalent: snapEq || undefined })
      if (!viewRequest.isCurrent()) return false
      const payload = toPayload(snap)
      applySnapshotPayload(payload)
      fullSnapshot = payload

      // Auto-select primary equivalent if not set or invalid
      const currentEq = normalizeEquivalentCode(opts.eq.value)
      if (!currentEq || !availableEquivalents.value.includes(currentEq)) {
        opts.eq.value = computePrimaryEquivalent(payload.trustlines, payload.equivalents)
        autoSelectedEq.value = normalizeEquivalentCode(opts.eq.value)
      }
      return true
    } catch (e: unknown) {
      if (!viewRequest.isCurrent()) return false
      error.value = describeError(e, 'graph.data.loadFailed').text
      return false
    } finally {
      if (viewRequest.isCurrent()) loading.value = false
    }
  }

  async function refreshSnapshotForEq(): Promise<boolean> {
    if (opts.focusMode.value) return false
    const request = viewRequests.begin()
    loading.value = true
    error.value = null
    try {
      const snapEq = normalizeEquivalentCode(opts.eq.value)
      const snap = await api.graphSnapshot({ equivalent: snapEq || undefined })
      if (!request.isCurrent()) return false
      const payload = toPayload(snap)
      applySnapshotPayload(payload)
      fullSnapshot = payload
      return true
    } catch (e: unknown) {
      if (!request.isCurrent()) return false
      error.value = describeError(e, 'graph.data.loadFailed').text
      return false
    } finally {
      if (request.isCurrent()) loading.value = false
    }
  }

  async function refreshForFocusMode(): Promise<boolean> {
    const viewRequest = viewRequests.begin()
    loading.value = true
    error.value = null

    const query = buildFocusModeQuery({
      enabled: Boolean(opts.focusMode.value),
      rootPid: opts.focusRootPid.value,
      depth: opts.focusDepth.value,
      equivalent: opts.eq.value,
      statusFilter: opts.statusFilter.value,
    })

    if (!query) {
      if (!fullSnapshot) {
        applySnapshotPayload({ participants: [], trustlines: [], equivalents: [] })
        return await loadData()
      }
      applySnapshotPayload(fullSnapshot)
      loading.value = false
      return true
    }

    try {
      const ego = await api.graphEgo({
        pid: query.pid,
        depth: query.depth,
        equivalent: query.equivalent,
        status: query.status,
      })
      if (!viewRequest.isCurrent()) return false
      applySnapshotPayload(toPayload(ego))
      return true
    } catch (e: unknown) {
      if (!viewRequest.isCurrent()) return false
      const failure = describeError(e, 'graph.focusMode.loadFailed').text
      error.value = failure
      ElMessage.warning(failure)
      return false
    } finally {
      if (viewRequest.isCurrent()) loading.value = false
    }
  }

  function reloadCurrentView(): Promise<boolean> {
    return opts.focusMode.value ? refreshForFocusMode() : loadData()
  }

  return {
    loading,
    error,

    participants,
    trustlines,
    equivalents,

    availableEquivalents,
    eqAutoSelected,
    precisionByEq,
    participantByPid,
    filteredTrustlines,

    loadData,
    refreshSnapshotForEq,
    refreshForFocusMode,
    invalidateDataOwnership,
    reloadCurrentView,
  }
}
