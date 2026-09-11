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

// F-013-1 / T1302. The graph page reads `transactions` in the analytics drawer's Activity card,
// and until now it never asked the server for them: `graphSnapshot` built its query with
// `equivalent` alone, so the collection arrived empty on every load and the panel reported zero
// committed payments for a period it had never enquired about.
//
// WHY ALWAYS-ON AND NOT PER-CALL. The drawer is fed from the snapshot the page already holds; making
// the include conditional on the drawer being open would mean a second fetch on open, its own race
// against the snapshot in flight, and a visible flip of the completeness signal from "not asked" to
// "asked" while the user is reading it. The cost of asking unconditionally is bounded and measured:
// the server caps this collection at ADMIN_GRAPH_INCLUDE_MAX_TRANSACTIONS (50) rows of eight scalar
// fields, which is 25,569 B against a 196,738 B snapshot body - 13.0% - measured over the first 50
// rows of the shipped fixtures in `admin-ui/public/admin-fixtures/v1/datasets/`.
//
// THAT IS AN UPPER BOUND, NOT THE EXPECTED COST, and no second figure is given: a fixture row still
// carries `payload` and `signatures`, which the server projection does not publish, so the wire row
// is lighter by an amount NOTHING IN THIS TREE MEASURES. Two earlier editions of this comment are
// withdrawn - one quoted "~11.6 KB ... about 6%" from an undocumented measurement, the next a
// projection-aware figure no command reproduces - and a third edition claimed to withdraw the second
// while still quoting it, because the edit that removed it began one line below the sentence that
// carried it. The method behind the figure that remains, with the command, is in
// `specs/013-frontend-data-honesty/spec.md`; re-derive it rather than believing this comment.
// comment. There is no snapshot poll: the graph loads on mount, on an
// equivalent change, on entering/leaving focus mode and on an explicit retry, so this is not a
// per-second cost. Incidents and audit_log are NOT requested here - see the note on
// readCompleteness below.
const GRAPH_INCLUDE = ['transactions']

// F-013-1 / T1302. Build the completeness metadata out of a response, tolerating its absence.
//
// A server that does not send `included` has told us nothing about which optional collections its
// body carries; the canon does not mark the field required, so absence is a real case and not a bug.
// The honest reading of absence is the empty list - "we know of no collection we may draw a
// conclusion about" - never a guess that everything we asked for arrived.
//
// NOTE FOR WHOEVER TOUCHES `incidents` OR `audit_log` NEXT: this client asks for neither, so both
// arrive empty in real mode for the same reason transactions did.
//
// CORRECTED 2026-09-11, because the sentence that stood here was wrong in the direction that
// matters. It said `incidentCount` / `participantOps` are "structurally zero" in real mode. They are
// not: in real mode the ACTIVITY CARD takes the metrics branch, where both counters are computed
// server-side over the whole table (`app/core/admin/metrics.py`), and the snapshot's empty
// collections never reach them. The claim holds only on the fallback branch - mock mode, a request
// in flight, or `/metrics` having failed. What IS unavailable in real mode on every branch is the
// per-participant incident RATIO, which is snapshot-only by construction. The same defect in two
// more collections is therefore narrower than this note first claimed; it is out of F-013-1's scope
// and is reported rather than silently half-fixed.
export function readCompleteness(src: { included?: unknown; truncated?: unknown }): {
  included: string[]
  truncated: string[]
} {
  const asCodes = (v: unknown): string[] =>
    Array.isArray(v) ? v.map((x) => String(x || '').trim().toLowerCase()).filter(Boolean) : []
  return { included: asCodes(src.included), truncated: asCodes(src.truncated) }
}

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
  const fullCycleError = ref<string | null>(null)
  const focusCycleError = ref<string | null>(null)
  const participantCycleError = ref<string | null>(null)
  const activeParticipantPid = ref('')
  const participantCycleState = ref<'idle' | 'pending' | 'visible' | 'fallback'>('idle')
  const cycleDisplayOwner = ref<{ kind: 'full' } | { kind: 'participant'; pid: string }>({ kind: 'full' })
  const cycleError = computed(() => {
    if (opts.focusMode.value) return focusCycleError.value
    if (cycleDisplayOwner.value.kind === 'participant') return participantCycleError.value
    if (participantCycleState.value === 'fallback' && activeParticipantPid.value) {
      return participantCycleError.value ?? fullCycleError.value
    }
    return fullCycleError.value
  })
  const error = computed(() => viewError.value ?? cycleError.value)

  const participants = ref<Participant[]>([])
  const trustlines = ref<Trustline[]>([])
  const incidents = ref<Incident[]>([])
  const equivalents = ref<Equivalent[]>([])
  const debts = ref<Debt[]>([])
  const clearingCycles = ref<ClearingCycles | null>(null)
  const auditLog = ref<AuditLogEntry[]>([])
  const transactions = ref<Transaction[]>([])
  // F-013-1 / T1302. What the last response said it actually carried, and which of those it cut.
  // These are the only honest basis for "do we know anything about collection X" - an empty
  // `transactions` array is produced both by "we did not ask" and by "we asked and there are none".
  const included = ref<string[]>([])
  const truncated = ref<string[]>([])

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
  const viewRequests = useLatestRequest()
  const cycleRequests = useLatestRequest()
  const participantCycleRequests = useLatestRequest()

  function resetParticipantCycleVisibility() {
    activeParticipantPid.value = ''
    participantCycleState.value = 'idle'
    participantCycleError.value = null
    cycleDisplayOwner.value = { kind: 'full' }
    participantCycleRequests.invalidate()
    clearingCycles.value = fullClearingCycles
  }

  function invalidateDataOwnership() {
    viewRequests.invalidate()
    cycleRequests.invalidate()
    participantCycleRequests.invalidate()
    loading.value = false
  }

  function applySnapshotPayload(p: GraphSnapshotPayload) {
    participants.value = p.participants || []
    trustlines.value = p.trustlines || []
    incidents.value = p.incidents || []
    equivalents.value = p.equivalents || []
    debts.value = p.debts || []
    auditLog.value = p.audit_log || []
    transactions.value = p.transactions || []
    included.value = p.included || []
    truncated.value = p.truncated || []
  }

  async function loadData(): Promise<boolean> {
    const viewRequest = viewRequests.begin()
    const cycleRequest = cycleRequests.begin()
    resetParticipantCycleVisibility()
    loading.value = true
    viewError.value = null
    try {
      // First load without equivalent to get full trustlines list for primary equivalent computation
      const snapEq = normalizeEqCode(opts.eq.value)
      const [snap, cycleResult] = await Promise.all([
        api.graphSnapshot({ equivalent: snapEq || undefined, include: GRAPH_INCLUDE }),
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
        ...readCompleteness(s),
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
          if (cycleDisplayOwner.value.kind === 'full') {
            clearingCycles.value = nextClearingCycles
          }
          fullCycleError.value = null
        } catch (e: unknown) {
          const msg = e instanceof Error ? e.message : String(e)
          fullCycleError.value = msg || t('graph.data.loadFailed')
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
      const snap = await api.graphSnapshot({ equivalent: snapEq || undefined, include: GRAPH_INCLUDE })
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
        ...readCompleteness(s),
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
    focusCycleError.value = null

    const query = buildFocusModeQuery({
      enabled: Boolean(opts.focusMode.value),
      rootPid: opts.focusRootPid.value,
      depth: opts.focusDepth.value,
      equivalent: opts.eq.value,
      statusFilter: opts.statusFilter.value,
    })

    if (!query) {
      if (viewRequest.isCurrent() && !fullSnapshot) {
        applySnapshotPayload({
          participants: [],
          trustlines: [],
          incidents: [],
          equivalents: [],
          debts: [],
          audit_log: [],
          transactions: [],
          included: [],
          truncated: [],
        })
        return await loadData()
      }
      if (viewRequest.isCurrent()) applySnapshotPayload(fullSnapshot!)
      if (cycleRequest.isCurrent()) clearingCycles.value = fullClearingCycles
      if (viewRequest.isCurrent()) loading.value = false
      return viewRequest.isCurrent()
    }

    try {
      const [ego, cycleResult] = await Promise.all([
        api.graphEgo({
          pid: query.pid,
          depth: query.depth,
          equivalent: query.equivalent,
          status: query.status,
          include: GRAPH_INCLUDE,
        }),
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
        ...readCompleteness(e),
      }
      if (!viewRequest.isCurrent()) return false
      applySnapshotPayload(payload)
      if (cycleRequest.isCurrent()) {
        try {
          if (cycleResult.status === 'rejected') throw cycleResult.reason
          if (!activeParticipantPid.value) {
            clearingCycles.value = (assertSuccess(cycleResult.value) as ClearingCycles | null) ?? null
          }
          focusCycleError.value = null
        } catch (e: unknown) {
          if (viewRequest.isCurrent()) {
            const msg = e instanceof Error ? e.message : String(e)
            const failure = msg || t('graph.focusMode.loadFailed')
            focusCycleError.value = failure
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
    const retainsParticipantCycles =
      cycleDisplayOwner.value.kind === 'participant' && cycleDisplayOwner.value.pid === pid
    activeParticipantPid.value = pid
    participantCycleState.value = 'pending'
    participantCycleError.value = null
    if (!retainsParticipantCycles) {
      cycleDisplayOwner.value = { kind: 'full' }
      clearingCycles.value = fullClearingCycles
    }
    const participantRequest = participantCycleRequests.begin()
    try {
      const cc = await api.clearingCycles({ participant_pid: pid })
      if (!participantRequest.isCurrent() || activeParticipantPid.value !== pid) return false
      clearingCycles.value = (assertSuccess(cc) as ClearingCycles | null) ?? null
      cycleDisplayOwner.value = { kind: 'participant', pid }
      participantCycleState.value = 'visible'
      participantCycleError.value = null
      return true
    } catch (e: unknown) {
      if (!participantRequest.isCurrent() || activeParticipantPid.value !== pid) return false
      if (retainsParticipantCycles) {
        participantCycleState.value = 'visible'
      } else {
        participantCycleState.value = 'fallback'
        cycleDisplayOwner.value = { kind: 'full' }
        clearingCycles.value = fullClearingCycles
      }
      const msg = e instanceof Error ? e.message : String(e)
      participantCycleError.value = msg || t('graph.data.loadFailed')
      return false
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
    incidents,
    included,
    truncated,
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
    invalidateDataOwnership,
    reloadCurrentView,
  }
}
