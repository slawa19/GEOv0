import { computed, ref, type Ref } from 'vue'
import { ElMessage } from 'element-plus'

import { api } from '../api'
import { assertSuccess } from '../api/envelope'
import { t } from '../i18n'
import { buildFocusModeQuery } from '../pages/graph/graphPageHelpers'
import { useLatestRequest } from './useLatestRequest'
import type {
  AuditLogEntry,
  ClearingCycles,
  Debt,
  Equivalent,
  GraphSnapshotPayload,
  Incident,
  Participant,
  Transaction,
  Trustline,
} from '../pages/graph/graphTypes'

export function normalizeEqCode(v: string): string {
  return String(v || '').trim().toUpperCase()
}

/**
 * Compute the primary (most popular) equivalent based on active trustlines count.
 * Returns the equivalent code with the most trustlines, or first available, or empty string.
 */
export function computePrimaryEquivalent(trustlines: { equivalent: string; status?: string }[], equivalents: { code: string }[]): string {
  const countByEq = new Map<string, number>()
  for (const t of trustlines || []) {
    const status = String(t.status || '').toLowerCase()
    if (status !== 'active') continue
    const code = normalizeEqCode(t.equivalent)
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
  return first ? normalizeEqCode(first.code) : ''
}

export function filterTrustlinesByEqAndStatus(input: {
  trustlines: Trustline[]
  equivalent: string
  statusFilter: string[]
}): Trustline[] {
  const eqKey = normalizeEqCode(input.equivalent)
  const allowed = new Set((input.statusFilter || []).map((s) => String(s).toLowerCase()).filter(Boolean))

  return (input.trustlines || []).filter((t) => {
    // Filter by equivalent (empty = show all)
    if (eqKey && normalizeEqCode(t.equivalent) !== eqKey) return false
    if (allowed.size && !allowed.has(String(t.status || '').toLowerCase())) return false
    return true
  })
}

export function computeIncidentRatioByPid(input: { incidents: Incident[]; equivalent: string }): Map<string, number> {
  const eqKey = normalizeEqCode(input.equivalent)
  const ratios = new Map<string, number>()

  for (const i of input.incidents || []) {
    // Filter by equivalent (empty = show all)
    if (eqKey && normalizeEqCode(i.equivalent) !== eqKey) continue
    const pid = String(i.initiator_pid || '').trim()
    if (!pid) continue
    const ratio = i.sla_seconds > 0 ? i.age_seconds / i.sla_seconds : 0
    const prev = ratios.get(pid) || 0
    if (ratio > prev) ratios.set(pid, ratio)
  }

  return ratios
}

export function useGraphData(opts: {
  eq: Ref<string>
  isRealMode: Ref<boolean>
  focusMode: Ref<boolean>
  focusRootPid: Ref<string>
  focusDepth: Ref<number>
  statusFilter: Ref<string[]>
}) {
  const loading = ref(false)
  const viewError = ref<string | null>(null)
  const cycleError = ref<string | null>(null)
  const error = computed(() => viewError.value ?? cycleError.value)

  const participants = ref<Participant[]>([])
  const trustlines = ref<Trustline[]>([])
  const incidents = ref<Incident[]>([])
  const equivalents = ref<Equivalent[]>([])
  const debts = ref<Debt[]>([])
  const clearingCycles = ref<ClearingCycles | null>(null)
  const auditLog = ref<AuditLogEntry[]>([])
  const transactions = ref<Transaction[]>([])

  const availableEquivalents = computed(() => {
    const fromDs = (equivalents.value || []).map((e) => normalizeEqCode(e.code)).filter(Boolean)
    const fromTls = (trustlines.value || []).map((t) => normalizeEqCode(t.equivalent)).filter(Boolean)
    // Note: 'ALL' option removed — now we always select a specific equivalent for proper viz_* support
    return Array.from(new Set([...fromDs, ...fromTls])).sort()
  })

  const precisionByEq = computed(() => {
    const m = new Map<string, number>()
    for (const e of equivalents.value || []) {
      const code = normalizeEqCode(e.code)
      if (!code) continue
      const p = Number(e.precision)
      if (Number.isFinite(p)) m.set(code, p)
    }
    return m
  })

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

  const incidentRatioByPid = computed(() => {
    return computeIncidentRatioByPid({ incidents: incidents.value || [], equivalent: opts.eq.value })
  })

  let fullSnapshot: GraphSnapshotPayload | null = null
  let fullClearingCycles: ClearingCycles | null = null
  let activeParticipantPid = ''
  const viewRequests = useLatestRequest()
  const cycleRequests = useLatestRequest()
  const participantCycleRequests = useLatestRequest()

  function resetParticipantCycleVisibility() {
    activeParticipantPid = ''
    participantCycleRequests.invalidate()
    clearingCycles.value = fullClearingCycles
  }

  function applySnapshotPayload(p: GraphSnapshotPayload) {
    participants.value = p.participants || []
    trustlines.value = p.trustlines || []
    incidents.value = p.incidents || []
    equivalents.value = p.equivalents || []
    debts.value = p.debts || []
    auditLog.value = p.audit_log || []
    transactions.value = p.transactions || []
  }

  async function loadData(): Promise<boolean> {
    const viewRequest = viewRequests.begin()
    const cycleRequest = cycleRequests.begin()
    resetParticipantCycleVisibility()
    loading.value = true
    viewError.value = null
    cycleError.value = null
    try {
      // First load without equivalent to get full trustlines list for primary equivalent computation
      const snapEq = normalizeEqCode(opts.eq.value)
      const [snap, cycleResult] = await Promise.all([
        api.graphSnapshot({ equivalent: snapEq || undefined }),
        api.clearingCycles().then(
          (value) => ({ status: 'fulfilled' as const, value }),
          (reason: unknown) => ({ status: 'rejected' as const, reason }),
        ),
      ])
      const s = assertSuccess(snap)
      const payload: GraphSnapshotPayload = {
        participants: (s.participants || []) as Participant[],
        trustlines: (s.trustlines || []) as Trustline[],
        incidents: (s.incidents || []) as Incident[],
        equivalents: (s.equivalents || []) as Equivalent[],
        debts: (s.debts || []) as Debt[],
        audit_log: (s.audit_log || []) as AuditLogEntry[],
        transactions: (s.transactions || []) as Transaction[],
      }

      if (viewRequest.isCurrent()) {
        applySnapshotPayload(payload)
        fullSnapshot = payload

        // Auto-select primary equivalent if not set or invalid
        const currentEq = normalizeEqCode(opts.eq.value)
        if (!currentEq || !availableEquivalents.value.includes(currentEq)) {
          opts.eq.value = computePrimaryEquivalent(payload.trustlines, payload.equivalents)
        }
      }

      if (cycleRequest.isCurrent()) {
        try {
          if (cycleResult.status === 'rejected') throw cycleResult.reason
          const nextClearingCycles = (assertSuccess(cycleResult.value) as ClearingCycles | null) ?? null
          fullClearingCycles = nextClearingCycles
          if (!activeParticipantPid) clearingCycles.value = nextClearingCycles
          cycleError.value = null
        } catch (e: unknown) {
          const msg = e instanceof Error ? e.message : String(e)
          cycleError.value = msg || t('graph.data.loadFailed')
        }
      }
      return viewRequest.isCurrent()
    } catch (e: unknown) {
      if (!viewRequest.isCurrent()) return false
      const msg = e instanceof Error ? e.message : String(e)
      viewError.value = msg || t('graph.data.loadFailed')
      return false
    } finally {
      if (viewRequest.isCurrent()) loading.value = false
    }
  }

  async function refreshSnapshotForEq(): Promise<boolean> {
    if (opts.focusMode.value) return false
    const request = viewRequests.begin()
    loading.value = true
    viewError.value = null
    try {
      const snapEq = normalizeEqCode(opts.eq.value)
      const snap = await api.graphSnapshot({ equivalent: snapEq || undefined })
      if (!request.isCurrent()) return false
      const s = assertSuccess(snap)
      const payload: GraphSnapshotPayload = {
        participants: (s.participants || []) as Participant[],
        trustlines: (s.trustlines || []) as Trustline[],
        incidents: (s.incidents || []) as Incident[],
        equivalents: (s.equivalents || []) as Equivalent[],
        debts: (s.debts || []) as Debt[],
        audit_log: (s.audit_log || []) as AuditLogEntry[],
        transactions: (s.transactions || []) as Transaction[],
      }
      applySnapshotPayload(payload)
      fullSnapshot = payload
      return true
    } catch (e: unknown) {
      if (!request.isCurrent()) return false
      const msg = e instanceof Error ? e.message : String(e)
      viewError.value = msg || t('graph.data.loadFailed')
      return false
    } finally {
      if (request.isCurrent()) loading.value = false
    }
  }

  async function refreshForFocusMode(): Promise<boolean> {
    // Mock focus mode only rebuilds the already-loaded snapshot. While the
    // initial/full load is pending there is no stable data for a watcher render.
    if (!opts.isRealMode.value) return !loading.value

    const viewRequest = viewRequests.begin()
    const cycleRequest = cycleRequests.begin()
    resetParticipantCycleVisibility()
    loading.value = true
    viewError.value = null
    cycleError.value = null

    const query = buildFocusModeQuery({
      enabled: Boolean(opts.focusMode.value),
      rootPid: opts.focusRootPid.value,
      depth: opts.focusDepth.value,
      equivalent: opts.eq.value,
      statusFilter: opts.statusFilter.value,
    })

    if (!query) {
      if (viewRequest.isCurrent() && fullSnapshot) applySnapshotPayload(fullSnapshot)
      if (cycleRequest.isCurrent()) clearingCycles.value = fullClearingCycles
      if (viewRequest.isCurrent()) loading.value = false
      return viewRequest.isCurrent()
    }

    try {
      const [ego, cycleResult] = await Promise.all([
        api.graphEgo({ pid: query.pid, depth: query.depth, equivalent: query.equivalent, status: query.status }),
        api.clearingCycles({ participant_pid: query.participant_pid }).then(
          (value) => ({ status: 'fulfilled' as const, value }),
          (reason: unknown) => ({ status: 'rejected' as const, reason }),
        ),
      ])

      const e = assertSuccess(ego) as Partial<GraphSnapshotPayload>
      const payload: GraphSnapshotPayload = {
        participants: (e.participants || []) as Participant[],
        trustlines: (e.trustlines || []) as Trustline[],
        incidents: (e.incidents || []) as Incident[],
        equivalents: (e.equivalents || []) as Equivalent[],
        debts: (e.debts || []) as Debt[],
        audit_log: (e.audit_log || []) as AuditLogEntry[],
        transactions: (e.transactions || []) as Transaction[],
      }
      if (!viewRequest.isCurrent()) return false
      applySnapshotPayload(payload)
      if (cycleRequest.isCurrent()) {
        try {
          if (cycleResult.status === 'rejected') throw cycleResult.reason
          if (!activeParticipantPid) {
            clearingCycles.value = (assertSuccess(cycleResult.value) as ClearingCycles | null) ?? null
          }
          cycleError.value = null
        } catch (e: unknown) {
          if (viewRequest.isCurrent()) {
            const msg = e instanceof Error ? e.message : String(e)
            const failure = msg || t('graph.focusMode.loadFailed')
            cycleError.value = failure
            ElMessage.warning(failure)
          }
        }
      }
      return true
    } catch (e: unknown) {
      if (!viewRequest.isCurrent()) return false
      const msg = e instanceof Error ? e.message : String(e)
      const failure = msg || t('graph.focusMode.loadFailed')
      viewError.value = failure
      ElMessage.warning(failure)
      return false
    } finally {
      if (viewRequest.isCurrent()) loading.value = false
    }
  }

  async function refreshClearingCyclesForParticipant(pid: string): Promise<boolean> {
    if (!pid) {
      resetParticipantCycleVisibility()
      return true
    }
    activeParticipantPid = pid
    const participantRequest = participantCycleRequests.begin()
    try {
      const cc = await api.clearingCycles({ participant_pid: pid })
      if (!participantRequest.isCurrent() || activeParticipantPid !== pid) return false
      clearingCycles.value = (assertSuccess(cc) as ClearingCycles | null) ?? null
      cycleError.value = null
      return true
    } catch {
      return participantRequest.isCurrent() && activeParticipantPid === pid
    }
  }

  return {
    loading,
    error,

    participants,
    trustlines,
    incidents,
    equivalents,
    debts,
    clearingCycles,
    auditLog,
    transactions,

    availableEquivalents,
    precisionByEq,
    participantByPid,
    filteredTrustlines,
    incidentRatioByPid,

    loadData,
    refreshSnapshotForEq,
    refreshForFocusMode,
    refreshClearingCyclesForParticipant,
  }
}
