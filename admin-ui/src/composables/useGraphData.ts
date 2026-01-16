import { computed, ref, type Ref } from 'vue'
import { ElMessage } from 'element-plus'

import { api } from '../api'
import { assertSuccess } from '../api/envelope'
import { buildFocusModeQuery } from '../pages/graph/graphPageHelpers'
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

export function filterTrustlinesByEqAndStatus(input: {
  trustlines: Trustline[]
  equivalent: string
  statusFilter: string[]
}): Trustline[] {
  const eqKey = normalizeEqCode(input.equivalent)
  const allowed = new Set((input.statusFilter || []).map((s) => String(s).toLowerCase()).filter(Boolean))

  return (input.trustlines || []).filter((t) => {
    if (eqKey !== 'ALL' && normalizeEqCode(t.equivalent) !== eqKey) return false
    if (allowed.size && !allowed.has(String(t.status || '').toLowerCase())) return false
    return true
  })
}

export function computeIncidentRatioByPid(input: { incidents: Incident[]; equivalent: string }): Map<string, number> {
  const eqKey = normalizeEqCode(input.equivalent)
  const ratios = new Map<string, number>()

  for (const i of input.incidents || []) {
    if (eqKey !== 'ALL' && normalizeEqCode(i.equivalent) !== eqKey) continue
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
  const error = ref<string | null>(null)

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
    const all = Array.from(new Set([...fromDs, ...fromTls])).sort()
    return ['ALL', ...all]
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

  function applySnapshotPayload(p: GraphSnapshotPayload) {
    participants.value = p.participants || []
    trustlines.value = p.trustlines || []
    incidents.value = p.incidents || []
    equivalents.value = p.equivalents || []
    debts.value = p.debts || []
    auditLog.value = p.audit_log || []
    transactions.value = p.transactions || []
  }

  async function loadData() {
    loading.value = true
    error.value = null
    try {
      const [snap, cc] = await Promise.all([api.graphSnapshot(), api.clearingCycles()])

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

      clearingCycles.value = (assertSuccess(cc) as ClearingCycles | null) ?? null

      fullSnapshot = payload
      fullClearingCycles = clearingCycles.value

      if (!availableEquivalents.value.includes(opts.eq.value)) opts.eq.value = 'ALL'
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      error.value = msg || 'Failed to load fixtures'
    } finally {
      loading.value = false
    }
  }

  let focusReqId = 0
  async function refreshForFocusMode() {
    if (!opts.isRealMode.value) return

    focusReqId += 1
    const reqId = focusReqId

    const query = buildFocusModeQuery({
      enabled: Boolean(opts.focusMode.value),
      rootPid: opts.focusRootPid.value,
      depth: opts.focusDepth.value,
      equivalent: opts.eq.value,
      statusFilter: opts.statusFilter.value,
    })

    if (!query) {
      if (fullSnapshot) applySnapshotPayload(fullSnapshot)
      clearingCycles.value = fullClearingCycles
      return
    }

    try {
      const [ego, cc] = await Promise.all([
        api.graphEgo({ pid: query.pid, depth: query.depth, equivalent: query.equivalent, status: query.status }),
        api.clearingCycles({ participant_pid: query.participant_pid }),
      ])

      if (reqId !== focusReqId) return

      const e = assertSuccess(ego) as Partial<GraphSnapshotPayload>
      applySnapshotPayload({
        participants: (e.participants || []) as Participant[],
        trustlines: (e.trustlines || []) as Trustline[],
        incidents: (e.incidents || []) as Incident[],
        equivalents: (e.equivalents || []) as Equivalent[],
        debts: (e.debts || []) as Debt[],
        audit_log: (e.audit_log || []) as AuditLogEntry[],
        transactions: (e.transactions || []) as Transaction[],
      })

      clearingCycles.value = (assertSuccess(cc) as ClearingCycles | null) ?? null
    } catch (e: unknown) {
      if (reqId !== focusReqId) return
      const msg = e instanceof Error ? e.message : String(e)
      ElMessage.warning(msg || 'Failed to load focus-mode data')
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
    refreshForFocusMode,
  }
}
