<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import cytoscape, { type Core, type EdgeSingular, type NodeSingular } from 'cytoscape'
import fcose from 'cytoscape-fcose'

import { loadFixtureJson } from '../api/fixtures'
import { formatDecimalFixed, isRatioBelowThreshold } from '../utils/decimal'
import { throttle } from '../utils/throttle'
import TooltipLabel from '../ui/TooltipLabel.vue'

cytoscape.use(fcose)

type Participant = { pid: string; display_name?: string; type?: string; status?: string }

type Trustline = {
  equivalent: string
  from: string
  to: string
  limit: string
  used: string
  available: string
  status: string
  created_at: string
}

type Debt = {
  equivalent: string
  debtor: string
  creditor: string
  amount: string
}

type ClearingCycles = {
  equivalents: Record<
    string,
    {
      cycles: Array<
        Array<{
          equivalent: string
          debtor: string
          creditor: string
          amount: string
        }>
      >
    }
  >
}

type Incident = {
  tx_id: string
  state: string
  initiator_pid: string
  equivalent: string
  age_seconds: number
  sla_seconds: number
  created_at?: string
}

type AuditLogEntry = {
  id: string
  timestamp: string
  actor_id?: string
  actor_role?: string
  action: string
  object_type: string
  object_id: string
  reason?: string | null
  before_state?: unknown
  after_state?: unknown
  request_id?: string
  ip_address?: string
}

type Transaction = {
  id?: string
  tx_id: string
  idempotency_key?: string | null
  type: string
  initiator_pid: string
  payload: Record<string, unknown>
  signatures?: unknown[] | null
  state: string
  error?: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

type Equivalent = { code: string; precision: number; description: string; is_active: boolean }

type SelectedInfo =
  | { kind: 'node'; pid: string; display_name?: string; type?: string; status?: string; degree: number; inDegree: number; outDegree: number }
  | { kind: 'edge'; id: string; equivalent: string; from: string; to: string; status: string; limit: string; used: string; available: string; created_at: string }

const loading = ref(false)
const error = ref<string | null>(null)

const cyRoot = ref<HTMLElement | null>(null)
let cy: Core | null = null
let zoomUpdatingFromCy = false

const participants = ref<Participant[]>([])
const trustlines = ref<Trustline[]>([])
const incidents = ref<Incident[]>([])
const equivalents = ref<Equivalent[]>([])
const debts = ref<Debt[]>([])
const clearingCycles = ref<ClearingCycles | null>(null)
const auditLog = ref<AuditLogEntry[]>([])
const transactions = ref<Transaction[]>([])

type DrawerTab = 'summary' | 'balance' | 'counterparties' | 'risk' | 'cycles'
const drawerTab = ref<DrawerTab>('summary')
const drawerEq = ref<string>('ALL')

type AnalyticsToggles = {
  showRank: boolean
  showDistribution: boolean
  showConcentration: boolean
  showCapacity: boolean
  showBottlenecks: boolean
  showActivity: boolean
}

const analytics = ref<AnalyticsToggles>({
  showRank: true,
  showDistribution: true,
  showConcentration: true,
  showCapacity: true,
  showBottlenecks: true,
  showActivity: true,
})

const seedLabel = computed(() => {
  const n = (participants.value || []).length
  const first = String(participants.value?.[0]?.display_name || '').toLowerCase()
  if (!n) return 'Seed: (not loaded)'

  if (n === 100 && first.includes('greenfield')) return 'Seed: Greenfield (100)'
  if (n === 50 && first.includes('riverside')) return 'Seed: Riverside (50)'

  // Fallback: still useful when experimenting with custom seeds.
  const prefix = first ? `, first: ${participants.value?.[0]?.display_name}` : ''
  return `Seed: ${n} participants${prefix}`
})

const eq = ref<string>('ALL')
const statusFilter = ref<string[]>(['active', 'frozen', 'closed'])
const threshold = ref<string>('0.10')

const typeFilter = ref<string[]>(['person', 'business'])
const minDegree = ref<number>(0)

type LabelMode = 'off' | 'name' | 'pid' | 'both'

const showLabels = ref(true)
const labelModeBusiness = ref<LabelMode>('name')
const labelModePerson = ref<LabelMode>('name')
const autoLabelsByZoom = ref(true)
const minZoomLabelsAll = ref(0.85)
const minZoomLabelsPerson = ref(1.25)

const showIncidents = ref(true)
const hideIsolates = ref(true)
const showLegend = ref(false)

const layoutName = ref<'fcose' | 'grid' | 'circle'>('fcose')
const layoutSpacing = ref<number>(2.2)

const toolbarTab = ref<'filters' | 'display' | 'navigate'>('filters')

const STORAGE_KEYS = {
  showLegend: 'geo.graph.showLegend',
  layoutSpacing: 'geo.graph.layoutSpacing',
  toolbarTab: 'geo.graph.toolbarTab',
  drawerEq: 'geo.graph.analytics.drawerEq',
  analyticsToggles: 'geo.graph.analytics.toggles.v1',
} as const

const zoom = ref<number>(1)

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n))
}

function zoomScale(z: number): number {
  // Smooth curve: zoom 0.25..3 => scale ~0.5..1.7
  return Math.sqrt(Math.max(0.05, z))
}

function extractPidFromText(text: string): string | null {
  const m = String(text || '').match(/PID_[A-Za-z0-9]+_[A-Za-z0-9]+/)
  return m ? m[0] : null
}

type LabelPart = 'name' | 'pid'

function labelPartsToMode(parts: LabelPart[]): LabelMode {
  const s = new Set(parts || [])
  if (s.size === 0) return 'off'
  if (s.has('name') && s.has('pid')) return 'both'
  if (s.has('pid')) return 'pid'
  return 'name'
}

function modeToLabelParts(mode: LabelMode): LabelPart[] {
  if (mode === 'both') return ['name', 'pid']
  if (mode === 'pid') return ['pid']
  if (mode === 'name') return ['name']
  return []
}

const businessLabelParts = computed<LabelPart[]>({
  get: () => modeToLabelParts(labelModeBusiness.value),
  set: (parts) => {
    labelModeBusiness.value = labelPartsToMode(parts)
  },
})

const personLabelParts = computed<LabelPart[]>({
  get: () => modeToLabelParts(labelModePerson.value),
  set: (parts) => {
    labelModePerson.value = labelPartsToMode(parts)
  },
})
type ParticipantSuggestion = { value: string; pid: string }

const searchQuery = ref('')
const focusPid = ref('')

const drawerOpen = ref(false)
const selected = ref<SelectedInfo | null>(null)

function money(v: string): string {
  return formatDecimalFixed(v, 2)
}

function pct(x: number, digits = 0): string {
  if (!Number.isFinite(x)) return '0%'
  const p = clamp(x * 100, 0, 100)
  return `${p.toFixed(digits)}%`
}

function parseIsoMillis(ts?: string | null): number | null {
  const s = String(ts || '').trim()
  if (!s) return null
  const ms = Date.parse(s)
  return Number.isFinite(ms) ? ms : null
}

function decimalToAtoms(input: string, precision: number): bigint {
  const raw = String(input ?? '').trim()
  if (!raw) return 0n
  const m = /^(-)?(\d+)(?:\.(\d+))?$/.exec(raw)
  if (!m) return 0n
  const neg = Boolean(m[1])
  const intPart = m[2] || '0'
  const fracPart = m[3] || ''
  const frac = (fracPart + '0'.repeat(precision)).slice(0, precision)
  const atoms = BigInt((intPart + frac).replace(/^0+(?=\d)/, '') || '0')
  return neg ? -atoms : atoms
}

function atomsToDecimal(atoms: bigint, precision: number): string {
  const neg = atoms < 0n
  const abs = neg ? -atoms : atoms
  const s = abs.toString()
  if (precision <= 0) return (neg ? '-' : '') + s
  const pad = precision + 1
  const padded = s.length >= pad ? s : '0'.repeat(pad - s.length) + s
  const head = padded.slice(0, padded.length - precision)
  const frac = padded.slice(padded.length - precision)
  return (neg ? '-' : '') + head + '.' + frac
}

async function loadOptionalFixtureJson<T>(relPath: string, fallback: T): Promise<T> {
  try {
    return await loadFixtureJson<T>(relPath)
  } catch {
    return fallback
  }
}

function normEq(v: string): string {
  return String(v || '').trim().toUpperCase()
}

function isBottleneck(t: Trustline): boolean {
  return isRatioBelowThreshold({ numerator: t.available, denominator: t.limit, threshold: threshold.value })
}

const availableEquivalents = computed(() => {
  const fromDs = (equivalents.value || []).map((e) => e.code).filter(Boolean)
  const fromTls = (trustlines.value || []).map((t) => normEq(t.equivalent)).filter(Boolean)
  const all = Array.from(new Set([...fromDs, ...fromTls])).sort()
  return ['ALL', ...all]
})

const precisionByEq = computed(() => {
  const m = new Map<string, number>()
  for (const e of equivalents.value || []) {
    const code = normEq(e.code)
    if (!code) continue
    const p = Number(e.precision)
    if (Number.isFinite(p)) m.set(code, p)
  }
  return m
})

const analyticsEq = computed(() => {
  const key = normEq(drawerEq.value)
  return key === 'ALL' ? null : key
})

const participantByPid = computed(() => {
  const m = new Map<string, Participant>()
  for (const p of participants.value || []) {
    if (p?.pid) m.set(p.pid, p)
  }
  return m
})

type BalanceRow = {
  equivalent: string
  outgoing_limit: string
  outgoing_used: string
  incoming_limit: string
  incoming_used: string
  total_debt: string
  total_credit: string
  net: string
}

const selectedBalanceRows = computed<BalanceRow[]>(() => {
  if (!selected.value || selected.value.kind !== 'node') return []
  const pid = selected.value.pid
  const precOf = (eqCode: string) => precisionByEq.value.get(eqCode) ?? 2

  const debtAtomsByEq = new Map<string, bigint>()
  const creditAtomsByEq = new Map<string, bigint>()

  for (const d of debts.value || []) {
    const eqCode = normEq(d.equivalent)
    if (!eqCode) continue
    const p = precOf(eqCode)
    const amt = decimalToAtoms(d.amount, p)
    if (d.debtor === pid) debtAtomsByEq.set(eqCode, (debtAtomsByEq.get(eqCode) || 0n) + amt)
    if (d.creditor === pid) creditAtomsByEq.set(eqCode, (creditAtomsByEq.get(eqCode) || 0n) + amt)
  }

  const outLimitByEq = new Map<string, bigint>()
  const outUsedByEq = new Map<string, bigint>()
  const inLimitByEq = new Map<string, bigint>()
  const inUsedByEq = new Map<string, bigint>()

  for (const t of trustlines.value || []) {
    const eqCode = normEq(t.equivalent)
    if (!eqCode) continue
    const p = precOf(eqCode)
    const lim = decimalToAtoms(t.limit, p)
    const used = decimalToAtoms(t.used, p)
    if (t.from === pid) {
      outLimitByEq.set(eqCode, (outLimitByEq.get(eqCode) || 0n) + lim)
      outUsedByEq.set(eqCode, (outUsedByEq.get(eqCode) || 0n) + used)
    }
    if (t.to === pid) {
      inLimitByEq.set(eqCode, (inLimitByEq.get(eqCode) || 0n) + lim)
      inUsedByEq.set(eqCode, (inUsedByEq.get(eqCode) || 0n) + used)
    }
  }

  const eqs = (availableEquivalents.value || []).filter((x) => x !== 'ALL')
  const wanted = analyticsEq.value ? [analyticsEq.value] : eqs

  return wanted
    .map((eqCode) => {
      const p = precOf(eqCode)
      const debt = debtAtomsByEq.get(eqCode) || 0n
      const credit = creditAtomsByEq.get(eqCode) || 0n
      const net = credit - debt
      return {
        equivalent: eqCode,
        outgoing_limit: atomsToDecimal(outLimitByEq.get(eqCode) || 0n, p),
        outgoing_used: atomsToDecimal(outUsedByEq.get(eqCode) || 0n, p),
        incoming_limit: atomsToDecimal(inLimitByEq.get(eqCode) || 0n, p),
        incoming_used: atomsToDecimal(inUsedByEq.get(eqCode) || 0n, p),
        total_debt: atomsToDecimal(debt, p),
        total_credit: atomsToDecimal(credit, p),
        net: atomsToDecimal(net, p),
      }
    })
    .filter((r) => {
      // Hide completely empty rows when eq=ALL.
      if (analyticsEq.value) return true
      const p = precOf(r.equivalent)
      return !(
        decimalToAtoms(r.outgoing_limit, p) === 0n &&
        decimalToAtoms(r.incoming_limit, p) === 0n &&
        decimalToAtoms(r.total_debt, p) === 0n &&
        decimalToAtoms(r.total_credit, p) === 0n
      )
    })
})

// Note: we keep split counterparties (creditors vs debtors) as the primary UI.

type CounterpartySplitRow = {
  pid: string
  display_name: string
  amount: string
  share: number
}

const selectedCounterpartySplit = computed(() => {
  if (!selected.value || selected.value.kind !== 'node') {
    return {
      eq: null as string | null,
      totalDebtAtoms: 0n,
      totalCreditAtoms: 0n,
      creditors: [] as CounterpartySplitRow[],
      debtors: [] as CounterpartySplitRow[],
    }
  }

  const eqCode = analyticsEq.value
  if (!eqCode) {
    return {
      eq: null as string | null,
      totalDebtAtoms: 0n,
      totalCreditAtoms: 0n,
      creditors: [] as CounterpartySplitRow[],
      debtors: [] as CounterpartySplitRow[],
    }
  }

  const pid = selected.value.pid
  const prec = precisionByEq.value.get(eqCode) ?? 2

  const creditors = new Map<string, bigint>() // who pid owes to (pid is debtor)
  const debtors = new Map<string, bigint>() // who owes pid (pid is creditor)

  let totalDebtAtoms = 0n
  let totalCreditAtoms = 0n

  for (const d of debts.value || []) {
    if (normEq(d.equivalent) !== eqCode) continue
    const amt = decimalToAtoms(d.amount, prec)
    if (d.debtor === pid) {
      totalDebtAtoms += amt
      const other = String(d.creditor || '').trim()
      if (other) creditors.set(other, (creditors.get(other) || 0n) + amt)
    } else if (d.creditor === pid) {
      totalCreditAtoms += amt
      const other = String(d.debtor || '').trim()
      if (other) debtors.set(other, (debtors.get(other) || 0n) + amt)
    }
  }

  const toRows = (m: Map<string, bigint>, total: bigint): CounterpartySplitRow[] => {
    const out: CounterpartySplitRow[] = []
    for (const [otherPid, amt] of m.entries()) {
      const p = participantByPid.value.get(otherPid)
      const name = String(p?.display_name || '').trim()
      const share = total > 0n ? Number(amt) / Number(total) : 0
      out.push({
        pid: otherPid,
        display_name: name || otherPid,
        amount: atomsToDecimal(amt, prec),
        share,
      })
    }
    out.sort((a, b) => {
      const ai = decimalToAtoms(a.amount, prec)
      const bi = decimalToAtoms(b.amount, prec)
      if (ai === bi) return a.pid.localeCompare(b.pid)
      return bi > ai ? 1 : -1
    })
    return out
  }

  return {
    eq: eqCode,
    totalDebtAtoms,
    totalCreditAtoms,
    creditors: toRows(creditors, totalDebtAtoms),
    debtors: toRows(debtors, totalCreditAtoms),
  }
})

function hhiFromShares(rows: Array<{ share: number }>): number {
  let hhi = 0
  for (const r of rows) {
    const s = Number(r.share) || 0
    hhi += s * s
  }
  return hhi
}

function hhiLevel(hhi: number): { label: string; type: 'success' | 'warning' | 'danger' } {
  if (hhi >= 0.25) return { label: 'high', type: 'danger' }
  if (hhi >= 0.15) return { label: 'medium', type: 'warning' }
  return { label: 'low', type: 'success' }
}

const selectedConcentration = computed(() => {
  const eqCode = selectedCounterpartySplit.value.eq
  if (!eqCode) {
    return {
      eq: null as string | null,
      outgoing: { top1: 0, top5: 0, hhi: 0, level: hhiLevel(0) },
      incoming: { top1: 0, top5: 0, hhi: 0, level: hhiLevel(0) },
    }
  }

  // outgoing = who you owe to (creditors map)
  const outRows = selectedCounterpartySplit.value.creditors
  const inRows = selectedCounterpartySplit.value.debtors

  const outTop1 = outRows[0]?.share || 0
  const outTop5 = outRows.slice(0, 5).reduce((acc, r) => acc + (r.share || 0), 0)
  const outHhi = hhiFromShares(outRows)

  const inTop1 = inRows[0]?.share || 0
  const inTop5 = inRows.slice(0, 5).reduce((acc, r) => acc + (r.share || 0), 0)
  const inHhi = hhiFromShares(inRows)

  return {
    eq: eqCode,
    outgoing: { top1: outTop1, top5: outTop5, hhi: outHhi, level: hhiLevel(outHhi) },
    incoming: { top1: inTop1, top5: inTop5, hhi: inHhi, level: hhiLevel(inHhi) },
  }
})

type NetDist = {
  eq: string
  n: number
  netAtomsByPid: Map<string, bigint>
  sortedPids: string[]
  min: bigint
  max: bigint
  bins: Array<{ from: bigint; to: bigint; count: number }>
}

const netDistribution = computed<NetDist | null>(() => {
  const eqCode = analyticsEq.value
  if (!eqCode) return null

  const prec = precisionByEq.value.get(eqCode) ?? 2
  const net = new Map<string, bigint>()

  for (const p of participants.value || []) {
    if (p?.pid) net.set(p.pid, 0n)
  }

  for (const d of debts.value || []) {
    if (normEq(d.equivalent) !== eqCode) continue
    const amt = decimalToAtoms(d.amount, prec)
    const debtor = String(d.debtor || '').trim()
    const creditor = String(d.creditor || '').trim()
    if (debtor) net.set(debtor, (net.get(debtor) || 0n) - amt)
    if (creditor) net.set(creditor, (net.get(creditor) || 0n) + amt)
  }

  const pids = Array.from(net.keys())
  pids.sort((a, b) => {
    const av = net.get(a) || 0n
    const bv = net.get(b) || 0n
    if (av === bv) return a.localeCompare(b)
    return bv > av ? 1 : -1
  })

  let min = 0n
  let max = 0n
  for (const v of net.values()) {
    if (v < min) min = v
    if (v > max) max = v
  }

  // Histogram
  const binsCount = 20
  const span = max - min
  const bins: Array<{ from: bigint; to: bigint; count: number }> = []
  if (span <= 0n) {
    bins.push({ from: min, to: max, count: pids.length })
  } else {
    // integer bucket width (ceil)
    const w = (span + BigInt(binsCount) - 1n) / BigInt(binsCount)
    for (let i = 0; i < binsCount; i++) {
      const from = min + BigInt(i) * w
      const to = i === binsCount - 1 ? max : from + w
      bins.push({ from, to, count: 0 })
    }
    for (const pid of pids) {
      const v = net.get(pid) || 0n
      const idx = w > 0n ? Number((v - min) / w) : 0
      const safe = clamp(idx, 0, binsCount - 1)
      const bucket = bins[safe]
      if (bucket) bucket.count += 1
    }
  }

  return { eq: eqCode, n: pids.length, netAtomsByPid: net, sortedPids: pids, min, max, bins }
})

const selectedRank = computed(() => {
  if (!selected.value || selected.value.kind !== 'node') return null
  const dist = netDistribution.value
  if (!dist) return null
  const idx = dist.sortedPids.indexOf(selected.value.pid)
  if (idx < 0) return null
  const rank = idx + 1
  const n = dist.n
  const percentile = n <= 1 ? 1 : (n - rank) / (n - 1) // 1.0 = top
  return {
    eq: dist.eq,
    rank,
    n,
    percentile,
    net: atomsToDecimal(dist.netAtomsByPid.get(selected.value.pid) || 0n, precisionByEq.value.get(dist.eq) ?? 2),
  }
})

const selectedCapacity = computed(() => {
  if (!selected.value || selected.value.kind !== 'node') return null
  const pid = selected.value.pid
  const eqCode = analyticsEq.value
  const precOf = (eqc: string) => precisionByEq.value.get(eqc) ?? 2

  let outLimit = 0n
  let outUsed = 0n
  let inLimit = 0n
  let inUsed = 0n

  const bottlenecks: Array<{ dir: 'out' | 'in'; other: string; t: Trustline }> = []

  for (const t of trustlines.value || []) {
    const e = normEq(t.equivalent)
    if (eqCode && e !== eqCode) continue
    const p = precOf(e)
    const lim = decimalToAtoms(t.limit, p)
    const used = decimalToAtoms(t.used, p)
    if (t.from === pid) {
      outLimit += lim
      outUsed += used
      if (isBottleneck(t)) bottlenecks.push({ dir: 'out', other: t.to, t })
    } else if (t.to === pid) {
      inLimit += lim
      inUsed += used
      if (isBottleneck(t)) bottlenecks.push({ dir: 'in', other: t.from, t })
    }
  }

  const outPct = outLimit > 0n ? Number(outUsed) / Number(outLimit) : 0
  const inPct = inLimit > 0n ? Number(inUsed) / Number(inLimit) : 0

  return {
    eq: eqCode,
    out: { limit: outLimit, used: outUsed, pct: outPct },
    inc: { limit: inLimit, used: inUsed, pct: inPct },
    bottlenecks,
  }
})

const selectedActivity = computed(() => {
  if (!selected.value || selected.value.kind !== 'node') return null
  const pid = selected.value.pid
  const eqCode = analyticsEq.value

  const tsCandidates: number[] = []
  for (const t of trustlines.value || []) {
    const ms = parseIsoMillis(t.created_at)
    if (ms !== null) tsCandidates.push(ms)
  }
  for (const i of incidents.value || []) {
    const ms = parseIsoMillis(i.created_at)
    if (ms !== null) tsCandidates.push(ms)
  }
  for (const a of auditLog.value || []) {
    const ms = parseIsoMillis(a.timestamp)
    if (ms !== null) tsCandidates.push(ms)
  }

  for (const t of transactions.value || []) {
    const ms = parseIsoMillis(t.updated_at || t.created_at)
    if (ms !== null) tsCandidates.push(ms)
  }

  const now = tsCandidates.length ? Math.max(...tsCandidates) : Date.now()
  const dayMs = 24 * 60 * 60 * 1000

  const windows = [7, 30, 90]
  const trustlineCreated: Record<number, number> = { 7: 0, 30: 0, 90: 0 }
  const trustlineClosed: Record<number, number> = { 7: 0, 30: 0, 90: 0 }
  const incidentCount: Record<number, number> = { 7: 0, 30: 0, 90: 0 }
  const participantOps: Record<number, number> = { 7: 0, 30: 0, 90: 0 }
  const paymentCommitted: Record<number, number> = { 7: 0, 30: 0, 90: 0 }
  const clearingCommitted: Record<number, number> = { 7: 0, 30: 0, 90: 0 }

  for (const t of trustlines.value || []) {
    if (eqCode && normEq(t.equivalent) !== eqCode) continue
    if (t.from !== pid && t.to !== pid) continue
    const ms = parseIsoMillis(t.created_at)
    if (ms === null) continue
    const ageDays = (now - ms) / dayMs
    for (const w of windows) {
      if (ageDays <= w) {
        trustlineCreated[w] = (trustlineCreated[w] ?? 0) + 1
        // NOTE: fixtures-first approximation.
        // This is NOT a "trustline closed event" counter.
        // It's: among trustlines created inside the window, how many are currently status=closed.
        // Do not copy this semantic into the future backend API (use event-based closed_at/audit-log instead).
        if (String(t.status || '').toLowerCase() === 'closed') trustlineClosed[w] = (trustlineClosed[w] ?? 0) + 1
      }
    }
  }

  for (const i of incidents.value || []) {
    if (eqCode && normEq(i.equivalent) !== eqCode) continue
    if (i.initiator_pid !== pid) continue
    const ms = parseIsoMillis(i.created_at)
    if (ms === null) continue
    const ageDays = (now - ms) / dayMs
    for (const w of windows) {
      if (ageDays <= w) incidentCount[w] = (incidentCount[w] ?? 0) + 1
    }
  }

  for (const a of auditLog.value || []) {
    if (String(a.object_id || '') !== pid) continue
    const action = String(a.action || '')
    if (!action.startsWith('PARTICIPANT_')) continue
    const ms = parseIsoMillis(a.timestamp)
    if (ms === null) continue
    const ageDays = (now - ms) / dayMs
    for (const w of windows) {
      if (ageDays <= w) participantOps[w] = (participantOps[w] ?? 0) + 1
    }
  }

  for (const tx of transactions.value || []) {
    const type = String(tx.type || '')
    if (type !== 'PAYMENT' && type !== 'CLEARING') continue
    if (String(tx.state || '') !== 'COMMITTED') continue

    const payload = (tx.payload || {}) as Record<string, unknown>
    const payloadEq = typeof payload.equivalent === 'string' ? payload.equivalent : null
    if (eqCode && payloadEq && normEq(payloadEq) !== eqCode) continue

    let involved = false
    if (type === 'PAYMENT') {
      const from = typeof payload.from === 'string' ? payload.from : ''
      const to = typeof payload.to === 'string' ? payload.to : ''
      involved = from === pid || to === pid
    } else {
      const edges = Array.isArray(payload.edges) ? (payload.edges as any[]) : []
      involved = edges.some((e) => e && (e.debtor === pid || e.creditor === pid))
      if (!involved) involved = String(tx.initiator_pid || '') === pid
    }
    if (!involved) continue

    const ms = parseIsoMillis(tx.updated_at || tx.created_at)
    if (ms === null) continue
    const ageDays = (now - ms) / dayMs
    for (const w of windows) {
      if (ageDays <= w) {
        if (type === 'PAYMENT') paymentCommitted[w] = (paymentCommitted[w] ?? 0) + 1
        if (type === 'CLEARING') clearingCommitted[w] = (clearingCommitted[w] ?? 0) + 1
      }
    }
  }

  return {
    windows,
    trustlineCreated,
    trustlineClosed,
    incidentCount,
    participantOps,
    paymentCommitted,
    clearingCommitted,
    hasTransactions: (transactions.value || []).length > 0,
  }
})

const selectedCycles = computed(() => {
  if (!selected.value || selected.value.kind !== 'node') return []
  const eqCode = analyticsEq.value
  if (!eqCode) return []
  const pid = selected.value.pid

  const cycles = clearingCycles.value?.equivalents?.[eqCode]?.cycles || []
  return cycles.filter((c) => c.some((e) => e.debtor === pid || e.creditor === pid)).slice(0, 10)
})

const filteredTrustlines = computed(() => {
  const eqKey = normEq(eq.value)
  const allowed = new Set((statusFilter.value || []).map((s) => String(s).toLowerCase()))
  return (trustlines.value || []).filter((t) => {
    if (eqKey !== 'ALL' && normEq(t.equivalent) !== eqKey) return false
    if (allowed.size && !allowed.has(String(t.status || '').toLowerCase())) return false
    return true
  })
})

const incidentRatioByPid = computed(() => {
  if (!showIncidents.value) return new Map<string, number>()

  const eqKey = normEq(eq.value)
  const ratios = new Map<string, number>()

  for (const i of incidents.value || []) {
    if (eqKey !== 'ALL' && normEq(i.equivalent) !== eqKey) continue
    const pid = String(i.initiator_pid || '').trim()
    if (!pid) continue
    const ratio = i.sla_seconds > 0 ? i.age_seconds / i.sla_seconds : 0
    const prev = ratios.get(pid) || 0
    if (ratio > prev) ratios.set(pid, ratio)
  }

  return ratios
})

function buildElements() {
  // 1) Start from trustlines filtered by non-type filters (equivalent/status/...).
  //    IMPORTANT: type filter must NOT affect isolate detection.
  const edgeCandidates = filteredTrustlines.value

  const allowedTypes = new Set((typeFilter.value || []).map((t) => String(t).toLowerCase()).filter(Boolean))
  const minDeg = Math.max(0, Number(minDegree.value) || 0)
  const focusedPid = String(focusPid.value || '').trim()

  const pIndex = new Map<string, Participant>()
  for (const p of participants.value || []) {
    if (p?.pid) pIndex.set(p.pid, p)
  }

  const typeOf = (pid: string): string => String(pIndex.get(pid)?.type || '').toLowerCase()
  const isTypeAllowed = (pid: string): boolean => {
    if (!allowedTypes.size) return true
    const t = typeOf(pid)
    return Boolean(t) && allowedTypes.has(t)
  }

  // Type filter applies to nodes and edges.
  // IMPORTANT (regression guard): do NOT drop trustline edges only because endpoint
  // types differ. When multiple types are selected (e.g. person+business), cross-type
  // trustlines are required to keep the graph connected. When a single type is selected,
  // cross-type edges are naturally filtered out because one endpoint won't be allowed.
  const isEdgeAllowedByType = (tl: Trustline): boolean => {
    return isTypeAllowed(tl.from) && isTypeAllowed(tl.to)
  }

  // 2) Global "has any edge" map: based on candidate trustlines ONLY (no type filter).
  //    This prevents "pseudo-isolates" when a node has edges, but only to hidden types.
  const hasAnyEdgeByPid = new Set<string>()
  for (const t of edgeCandidates) {
    hasAnyEdgeByPid.add(t.from)
    hasAnyEdgeByPid.add(t.to)
  }

  // 3) Visible edges = candidate trustlines filtered by type.
  const visibleEdges = edgeCandidates.filter(isEdgeAllowedByType)

  // 4) Visible nodes: endpoints of visible edges + (optionally) true isolates.
  const pidSet = new Set<string>()
  for (const t of visibleEdges) {
    pidSet.add(t.from)
    pidSet.add(t.to)
  }

  // Add isolates ONLY if they have no trustlines at all (under non-type filters).
  // Do NOT add nodes that have trustlines but all of them go to hidden types.
  if (!hideIsolates.value) {
    for (const p of participants.value || []) {
      if (!p?.pid) continue
      if (!isTypeAllowed(p.pid)) continue
      if (hasAnyEdgeByPid.has(p.pid)) continue
      pidSet.add(p.pid)
    }
  }

  const prelim = new Set<string>()
  for (const pid of pidSet) {
    if (!isTypeAllowed(pid)) continue
    prelim.add(pid)
  }

  const filteredEdges = visibleEdges.filter((t) => prelim.has(t.from) && prelim.has(t.to))

  const degreeByPid = new Map<string, number>()
  for (const t of filteredEdges) {
    degreeByPid.set(t.from, (degreeByPid.get(t.from) || 0) + 1)
    degreeByPid.set(t.to, (degreeByPid.get(t.to) || 0) + 1)
  }

  const finalPids = new Set<string>()
  for (const pid of prelim) {
    const deg = degreeByPid.get(pid) || 0
    if (minDeg > 0 && deg < minDeg && pid !== focusedPid) continue
    finalPids.add(pid)
  }

  const nodes = Array.from(finalPids).map((pid) => {
    const p = pIndex.get(pid)
    const ratio = incidentRatioByPid.value.get(pid)
    const name = (p?.display_name || '').trim()
    return {
      data: {
        id: pid,
        label: '',
        pid,
        display_name: name,
        status: (p?.status || '').toLowerCase(),
        type: (p?.type || '').toLowerCase(),
        incident_ratio: typeof ratio === 'number' ? ratio : 0,
      },
      classes: [
        (p?.status || '').toLowerCase() ? `p-${(p?.status || '').toLowerCase()}` : '',
        (p?.type || '').toLowerCase() ? `type-${(p?.type || '').toLowerCase()}` : '',
        showIncidents.value && (incidentRatioByPid.value.get(pid) || 0) > 0 ? 'has-incident' : '',
      ]
        .filter(Boolean)
        .join(' '),
    }
  })

  const edges = filteredEdges
    .filter((t) => finalPids.has(t.from) && finalPids.has(t.to))
    .map((t, idx) => {
    const bottleneck = t.status === 'active' && isBottleneck(t)
    const id = `tl_${idx}_${t.from}_${t.to}_${normEq(t.equivalent)}`
    const classes = [
      `tl-${String(t.status || '').toLowerCase()}`,
      bottleneck ? 'bottleneck' : '',
      showIncidents.value && (incidentRatioByPid.value.get(t.from) || 0) > 0 ? 'incident' : '',
    ]
      .filter(Boolean)
      .join(' ')

    return {
      data: {
        id,
        source: t.from,
        target: t.to,
        equivalent: normEq(t.equivalent),
        status: String(t.status || '').toLowerCase(),
        limit: t.limit,
        used: t.used,
        available: t.available,
        created_at: t.created_at,
        bottleneck: bottleneck ? 1 : 0,
      },
      classes,
    }
  })

  return { nodes, edges }
}

function applyStyle() {
  if (!cy) return

  cy.style([
    {
      selector: 'node',
      style: {
        'background-color': '#409eff',
        label: showLabels.value ? 'data(label)' : '',
        color: '#cfd3dc',
        // Base values; real sizes are adjusted by updateZoomStyles().
        'font-size': 11,
        // Allow fonts to become small when zoomed out.
        'min-zoomed-font-size': 4,
        'text-outline-width': 2,
        'text-outline-color': '#111318',
        'text-wrap': 'wrap',
        'text-max-width': '180px',
        'text-background-opacity': 0,
        'text-halign': 'center',
        'text-valign': 'bottom',
        'text-margin-y': 6,
        'border-width': 1,
        'border-color': '#2b2f36',
        width: 18,
        height: 18,
      },
    },
    { selector: 'node.p-active', style: { 'background-color': '#67c23a' } },
    { selector: 'node.p-frozen', style: { 'background-color': '#e6a23c' } },
    { selector: 'node.p-suspended', style: { 'background-color': '#e6a23c' } },
    { selector: 'node.p-banned', style: { 'background-color': '#f56c6c' } },
    { selector: 'node.p-deleted', style: { 'background-color': '#909399' } },

    { selector: 'node.type-person', style: { shape: 'ellipse', width: 16, height: 16 } },
    {
      selector: 'node.type-business',
      style: {
        shape: 'round-rectangle',
        width: 26,
        height: 22,
        'border-width': 2,
        'border-color': '#409eff',
      },
    },

    {
      selector: 'node.has-incident',
      style: {
        'border-width': 3,
        'border-color': '#f56c6c',
      },
    },

    {
      selector: 'node.search-hit',
      style: {
        'border-width': 4,
        'border-color': '#e6a23c',
      },
    },

    {
      selector: 'edge',
      style: {
        // Base values; real widths are adjusted by updateZoomStyles().
        width: 1.4,
        'curve-style': 'bezier',
        'line-color': '#606266',
        'target-arrow-shape': 'triangle',
        'target-arrow-color': '#606266',
        'arrow-scale': 0.8,
        opacity: 0.85,
      },
    },
    { selector: 'edge.tl-active', style: { 'line-color': '#409eff', 'target-arrow-color': '#409eff' } },
    { selector: 'edge.tl-frozen', style: { 'line-color': '#909399', 'target-arrow-color': '#909399', opacity: 0.65 } },
    { selector: 'edge.tl-closed', style: { 'line-color': '#a3a6ad', 'target-arrow-color': '#a3a6ad', opacity: 0.45 } },

    { selector: 'edge.bottleneck', style: { 'line-color': '#f56c6c', 'target-arrow-color': '#f56c6c', width: 2.8, 'arrow-scale': 0.95, opacity: 1 } },

    {
      selector: 'edge.incident',
      style: {
        'line-style': 'dashed',
      },
    },
  ])

  updateZoomStyles()
}

function updateZoomStyles() {
  if (!cy) return
  const z = cy.zoom()

  // Cytoscape scales stroke/labels by zoom; to avoid "fat" edges/text when zoomed in,
  // scale style values inversely with zoom.
  const inv = 1 / Math.max(0.15, z)
  const s = zoomScale(inv)

  const nodeFont = clamp(11 * inv, 3.2, 12)
  const outlineW = clamp(2 * s, 0.6, 2.4)
  const marginY = clamp(6 * inv, 1, 8)

  const edgeW = clamp(1.2 * inv, 0.25, 1.6)
  const edgeWBottleneck = clamp(2.4 * inv, 0.6, 3)
  const arrowScale = clamp(0.8 * s, 0.32, 1.0)
  const arrowScaleBottleneck = clamp(0.95 * s, 0.4, 1.15)

  cy.style()
    .selector('node')
    .style({
      'font-size': nodeFont,
      'text-outline-width': outlineW,
      'text-margin-y': marginY,
    })
    .selector('edge')
    .style({
      width: edgeW,
      'arrow-scale': arrowScale,
    })
    .selector('edge.bottleneck')
    .style({
      width: edgeWBottleneck,
      'arrow-scale': arrowScaleBottleneck,
    })
    .update()
}

function runLayout() {
  if (!cy) return

  const name = layoutName.value
  const spacing = Math.max(1, Math.min(3, Number(layoutSpacing.value) || 1))
  const layout =
    name === 'grid'
      ? cy.layout({ name: 'grid', padding: 30 })
      : name === 'circle'
        ? cy.layout({ name: 'circle', padding: 30 })
        : cy.layout({
            name: 'fcose',
            animate: false,
          randomize: true,
          randomSeed: 42,
            padding: 60,
            quality: spacing >= 1.4 ? 'proof' : 'default',
            nodeSeparation: Math.round(95 * spacing),
            idealEdgeLength: Math.round(120 * spacing),
            nodeRepulsion: Math.round(7200 * spacing * spacing),
            edgeElasticity: 0.35,
            gravity: 0.18,
            numIter: spacing >= 1.8 ? 3500 : 2500,
            avoidOverlap: true,
            nodeDimensionsIncludeLabels: true,
            packComponents: true,
          } as any)

  layout.run()
}

let layoutRunId = 0

function runLayoutAndMaybeFit({ fitOnStop }: { fitOnStop: boolean }) {
  if (!cy) return
  layoutRunId += 1
  const runId = layoutRunId

  if (fitOnStop) {
    cy.one('layoutstop', () => {
      if (!cy) return
      if (runId !== layoutRunId) return
      cy.fit(cy.elements(), 10)
      zoomUpdatingFromCy = true
      zoom.value = cy.zoom()
      zoomUpdatingFromCy = false
      updateZoomStyles()
      updateLabelsForZoom()
    })
  }

  runLayout()
}

function rebuildGraph({ fit }: { fit: boolean }) {
  if (!cy) return
  const { nodes, edges } = buildElements()
  cy.elements().remove()
  cy.add(nodes)
  cy.add(edges)

  applyStyle()
  updateZoomStyles()
  updateLabelsForZoom()
  updateSearchHighlights()
  runLayoutAndMaybeFit({ fitOnStop: fit })
}

function labelFor(mode: LabelMode, displayName: string, pid: string): string {
  if (mode === 'off') return ''
  if (mode === 'pid') return pid
  if (mode === 'name') return displayName || pid
  return displayName ? `${displayName}\n${pid}` : pid
}

function updateLabelsForZoom() {
  if (!cy) return

  if (!showLabels.value) {
    cy.nodes().forEach((n) => {
      n.data('label', '')
    })
    return
  }

  const z = cy.zoom()
  const ext = cy.extent()

  // Dynamic label visibility based on "how crowded" the current viewport is.
  // This avoids hard-coded zoom thresholds causing labels to disappear even when
  // only a small subset of nodes is on-screen.
  let nodesInView = 0
  cy.nodes().forEach((n) => {
    if (!n.visible()) return
    const p = n.position()
    if (p.x >= ext.x1 && p.x <= ext.x2 && p.y >= ext.y1 && p.y <= ext.y2) nodesInView += 1
  })

  // Heuristics tuned for ~100 nodes total; for crowded views, keep labels off.
  const allowBusinessByCount = nodesInView <= 85
  const allowPersonsByCount = nodesInView <= 55
  cy.nodes().forEach((n) => {
    const pid = String(n.data('pid') || n.id())
    const displayName = String(n.data('display_name') || '')
    const t = String(n.data('type') || '').toLowerCase()

    let mode: LabelMode = t === 'business' ? labelModeBusiness.value : labelModePerson.value

    if (autoLabelsByZoom.value) {
      const allowBusiness = z >= minZoomLabelsAll.value || allowBusinessByCount
      const allowPersons = z >= minZoomLabelsPerson.value || allowPersonsByCount

      if (!allowBusiness) {
        mode = 'off'
      } else if (t === 'person' && !allowPersons) {
        mode = 'off'
      } else if (z < 1.5 && mode === 'both') {
        mode = 'name'
      }
    }

    n.data('label', labelFor(mode, displayName, pid))
  })
}

function visibleParticipantSuggestions(): ParticipantSuggestion[] {
  const out: ParticipantSuggestion[] = []
  if (!cy) {
    for (const p of participants.value || []) {
      if (!p?.pid) continue
      const name = String(p.display_name || '').trim()
      out.push({ value: name ? `${name} — ${p.pid}` : p.pid, pid: p.pid })
    }
    return out
  }

  cy.nodes().forEach((n) => {
    const pid = String(n.data('pid') || n.id())
    const name = String(n.data('display_name') || '').trim()
    out.push({ value: name ? `${name} — ${pid}` : pid, pid })
  })

  return out
}

function querySearchParticipants(query: string, cb: (results: ParticipantSuggestion[]) => void) {
  const q = String(query || '').trim().toLowerCase()
  if (!q) {
    cb(visibleParticipantSuggestions().slice(0, 20))
    return
  }

  const results = visibleParticipantSuggestions()
    .filter((s) => s.value.toLowerCase().includes(q) || s.pid.toLowerCase().includes(q))
    .slice(0, 20)

  cb(results)
}

function onSearchSelect(s: ParticipantSuggestion) {
  focusPid.value = s.pid
}

function matchedVisiblePids(query: string): string[] {
  const q = String(query || '').trim().toLowerCase()
  if (!q) return []

  const pidHint = extractPidFromText(query)
  if (pidHint) return [pidHint]

  const matches: string[] = []

  if (cy) {
    cy.nodes().forEach((n) => {
      const pid = String(n.data('pid') || n.id())
      const name = String(n.data('display_name') || '')
      const combined = `${name} ${pid}`.toLowerCase()
      if (combined.includes(q)) matches.push(pid)
    })
    return matches
  }

  for (const p of participants.value || []) {
    const pid = String(p?.pid || '')
    const name = String(p?.display_name || '')
    if (!pid) continue
    const combined = `${name} ${pid}`.toLowerCase()
    if (combined.includes(q)) matches.push(pid)
  }

  return matches
}

function updateSearchHighlights() {
  if (!cy) return
  cy.nodes('.search-hit').removeClass('search-hit')

  const q = String(searchQuery.value || '').trim()
  if (!q) return

  const matches = matchedVisiblePids(q)
  // Cap highlighting to avoid turning the whole graph orange.
  for (const pid of matches.slice(0, 40)) {
    cy.getElementById(pid).addClass('search-hit')
  }
}

function attachHandlers() {
  if (!cy) return

  cy.on('tap', 'node', (ev) => {
    const n = ev.target as NodeSingular
    const pid = String(n.data('pid') || n.id())
    const degree = n.degree(false)
    const inDegree = n.indegree(false)
    const outDegree = n.outdegree(false)
    selected.value = {
      kind: 'node',
      pid,
      display_name: String(n.data('display_name') || '') || undefined,
      status: String(n.data('status') || '') || undefined,
      type: String(n.data('type') || '') || undefined,
      degree,
      inDegree,
      outDegree,
    }
    drawerTab.value = 'summary'
    drawerOpen.value = true
  })

  cy.on('tap', 'edge', (ev) => {
    const e = ev.target as EdgeSingular
    selected.value = {
      kind: 'edge',
      id: e.id(),
      equivalent: String(e.data('equivalent') || ''),
      from: String(e.data('source') || ''),
      to: String(e.data('target') || ''),
      status: String(e.data('status') || ''),
      limit: String(e.data('limit') || ''),
      used: String(e.data('used') || ''),
      available: String(e.data('available') || ''),
      created_at: String(e.data('created_at') || ''),
    }
    drawerOpen.value = true
  })
}

function fit() {
  if (!cy) return
  cy.fit(cy.elements(), 10)
  zoomUpdatingFromCy = true
  zoom.value = cy.zoom()
  zoomUpdatingFromCy = false
  updateZoomStyles()
  updateLabelsForZoom()
}

function focusSearch() {
  if (!cy) return

  const q = String(searchQuery.value || '').trim()
  const pidInQuery = extractPidFromText(q)

  // If query is empty, fall back to focused/selected node.
  if (!q) {
    const pid = getZoomPid()
    if (!pid) {
      ElMessage.info('Type PID/name, select a suggestion, or click a node')
      return
    }
    const n = cy.getElementById(pid)
    if (!n || n.empty()) {
      ElMessage.warning(`Not found in current graph: ${pid}`)
      return
    }
    cy.animate({ center: { eles: n }, zoom: Math.max(1.2, cy.zoom()) }, { duration: 300 })
    n.addClass('search-hit')
    setTimeout(() => n.removeClass('search-hit'), 900)
    return
  }

  // Prefer an explicit selection (autocomplete).
  if (focusPid.value) {
    const n = cy.getElementById(focusPid.value)
    if (!n || n.empty()) {
      ElMessage.warning(`Not found in current graph: ${focusPid.value}`)
      return
    }
    cy.animate({ center: { eles: n }, zoom: Math.max(1.2, cy.zoom()) }, { duration: 300 })
    n.addClass('search-hit')
    setTimeout(() => n.removeClass('search-hit'), 900)
    return
  }

  // PID embedded in "Name — PID" value.
  if (pidInQuery) {
    const n = cy.getElementById(pidInQuery)
    if (n && !n.empty()) {
      cy.animate({ center: { eles: n }, zoom: Math.max(1.2, cy.zoom()) }, { duration: 300 })
      n.addClass('search-hit')
      setTimeout(() => n.removeClass('search-hit'), 900)
      return
    }
  }

  // Exact PID match.
  const exact = cy.getElementById(q)
  if (exact && !exact.empty()) {
    cy.animate({ center: { eles: exact }, zoom: Math.max(1.2, cy.zoom()) }, { duration: 300 })
    exact.addClass('search-hit')
    setTimeout(() => exact.removeClass('search-hit'), 900)
    return
  }

  // Partial match by PID or display_name.
  const matches = matchedVisiblePids(q)
  if (matches.length === 0) {
    const fallbackPid = selected.value && selected.value.kind === 'node' ? selected.value.pid : ''
    if (fallbackPid) {
      const n = cy.getElementById(fallbackPid)
      if (n && !n.empty()) {
        cy.animate({ center: { eles: n }, zoom: Math.max(1.2, cy.zoom()) }, { duration: 300 })
        n.addClass('search-hit')
        setTimeout(() => n.removeClass('search-hit'), 900)
        ElMessage.info('Query did not match; centered on the selected node.')
        return
      }
    }
    ElMessage.warning(`No matches: ${q}`)
    return
  }

  if (matches.length === 1) {
    const pid = matches[0]
    if (!pid) return
    const n = cy.getElementById(pid)
    cy.animate({ center: { eles: n }, zoom: Math.max(1.2, cy.zoom()) }, { duration: 300 })
    n.addClass('search-hit')
    setTimeout(() => n.removeClass('search-hit'), 900)
    return
  }

  let eles = cy.collection()
  for (const pid of matches.slice(0, 40)) {
    eles = eles.union(cy.getElementById(pid))
  }
  cy.animate({ fit: { eles, padding: 80 } }, { duration: 300 })
  ElMessage.info(`${matches.length} matches (showing first ${Math.min(40, matches.length)}). Refine query.`)
}

async function loadData() {
  loading.value = true
  error.value = null
  try {
    const [ps, tls, inc, eqs, ds, cc, al, txs] = await Promise.all([
      loadFixtureJson<Participant[]>('datasets/participants.json'),
      loadFixtureJson<Trustline[]>('datasets/trustlines.json'),
      loadFixtureJson<{ items: Incident[] }>('datasets/incidents.json'),
      loadFixtureJson<Equivalent[]>('datasets/equivalents.json'),
      loadOptionalFixtureJson<Debt[]>('datasets/debts.json', []),
      loadOptionalFixtureJson<ClearingCycles | null>('datasets/clearing-cycles.json', null),
      loadOptionalFixtureJson<AuditLogEntry[]>('datasets/audit-log.json', []),
      loadOptionalFixtureJson<Transaction[]>('datasets/transactions.json', []),
    ])

    participants.value = ps
    trustlines.value = tls
    incidents.value = inc.items
    equivalents.value = eqs
    debts.value = ds
    clearingCycles.value = cc
    auditLog.value = al
    transactions.value = txs

    if (!availableEquivalents.value.includes(eq.value)) eq.value = 'ALL'
  } catch (e: any) {
    error.value = e?.message || 'Failed to load fixtures'
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    const rawLegend = window.localStorage.getItem(STORAGE_KEYS.showLegend)
    if (rawLegend !== null) showLegend.value = rawLegend === '1'

    const rawTab = window.localStorage.getItem(STORAGE_KEYS.toolbarTab)
    if (rawTab === 'filters' || rawTab === 'display' || rawTab === 'navigate') toolbarTab.value = rawTab

    const rawSpacing = window.localStorage.getItem(STORAGE_KEYS.layoutSpacing)
    if (rawSpacing !== null) {
      const parsed = Number(rawSpacing)
      if (Number.isFinite(parsed)) layoutSpacing.value = parsed
    }

    const rawDrawerEq = window.localStorage.getItem(STORAGE_KEYS.drawerEq)
    if (rawDrawerEq) drawerEq.value = String(rawDrawerEq)

    const rawToggles = window.localStorage.getItem(STORAGE_KEYS.analyticsToggles)
    if (rawToggles) {
      const parsed = JSON.parse(rawToggles) as Partial<AnalyticsToggles>
      analytics.value = {
        ...analytics.value,
        ...parsed,
      }
    }
  } catch {
    // ignore storage errors (private mode / blocked)
  }

  await loadData()

  if (!cyRoot.value) return
  cy = cytoscape({
    container: cyRoot.value,
    elements: [],
    minZoom: 0.1,
    maxZoom: 3,
    wheelSensitivity: 0.15,
  })

  cy.on('viewport', () => {
    if (!cy) return
    zoomUpdatingFromCy = true
    zoom.value = cy.zoom()
    zoomUpdatingFromCy = false

    // Keep styling/labels responsive to mouse wheel / pinch zoom + panning.
    updateZoomStyles()
    updateLabelsForZoom()
  })

  attachHandlers()
  rebuildGraph({ fit: true })

  zoomUpdatingFromCy = true
  zoom.value = cy.zoom()
  zoomUpdatingFromCy = false
})

watch(showLegend, (v) => {
  try {
    window.localStorage.setItem(STORAGE_KEYS.showLegend, v ? '1' : '0')
  } catch {
    // ignore
  }
})

watch(layoutSpacing, (v) => {
  try {
    window.localStorage.setItem(STORAGE_KEYS.layoutSpacing, String(v))
  } catch {
    // ignore
  }
})

watch(drawerEq, (v) => {
  try {
    window.localStorage.setItem(STORAGE_KEYS.drawerEq, String(v || 'ALL'))
  } catch {
    // ignore
  }
})

watch(
  analytics,
  (v) => {
    try {
      window.localStorage.setItem(STORAGE_KEYS.analyticsToggles, JSON.stringify(v))
    } catch {
      // ignore
    }
  },
  { deep: true }
)

watch(toolbarTab, (v) => {
  try {
    window.localStorage.setItem(STORAGE_KEYS.toolbarTab, v)
  } catch {
    // ignore
  }
})

onBeforeUnmount(() => {
  if (cy) {
    cy.destroy()
    cy = null
  }
})

const throttledRebuild = throttle(() => {
  if (!cy) return
  rebuildGraph({ fit: false })
}, 300)

watch([eq, statusFilter, threshold, showIncidents, hideIsolates], () => {
  throttledRebuild()
})

watch([typeFilter, minDegree], () => {
  throttledRebuild()
})

watch([showLabels, labelModeBusiness, labelModePerson, autoLabelsByZoom, minZoomLabelsAll, minZoomLabelsPerson], () => {
  if (!cy) return
  applyStyle()
  updateLabelsForZoom()
})

watch(searchQuery, () => {
  if (!cy) return
  // If user edits the query manually, clear explicit selection.
  focusPid.value = ''
  updateSearchHighlights()
})

watch(zoom, (z) => {
  if (!cy) return
  if (zoomUpdatingFromCy) return
  applyZoom(z)
  updateZoomStyles()
  updateLabelsForZoom()
})

watch(layoutName, () => {
  if (!cy) return
  runLayout()
})

watch(layoutSpacing, () => {
  if (!cy) return
  runLayout()
})

const statuses = [
  { label: 'active', value: 'active' },
  { label: 'frozen', value: 'frozen' },
  { label: 'closed', value: 'closed' },
]

const layoutOptions = [
  { label: 'fcose (force)', value: 'fcose' },
  { label: 'grid', value: 'grid' },
  { label: 'circle', value: 'circle' },
]

const stats = computed(() => {
  const { nodes, edges } = buildElements()
  const bottlenecks = edges.filter((e) => e.data?.bottleneck === 1).length
  return { nodes: nodes.length, edges: edges.length, bottlenecks }
})

function getZoomPid(): string | null {
  const pid = String(focusPid.value || '').trim()
  if (pid) return pid
  if (selected.value && selected.value.kind === 'node') return selected.value.pid
  return null
}

const canFind = computed(() => {
  const q = String(searchQuery.value || '').trim()
  if (q) return true
  return Boolean(getZoomPid())
})

function applyZoom(level: number) {
  if (!cy) return
  const z = Math.min(cy.maxZoom(), Math.max(cy.minZoom(), level))
  const center = { x: cy.width() / 2, y: cy.height() / 2 }
  cy.zoom({ level: z, renderedPosition: center })
}
</script>

<template>
  <el-card class="geoCard">
    <template #header>
      <div class="hdr">
        <TooltipLabel label="Network Graph" tooltip-key="nav.graph" />
        <div class="hdr__right">
          <el-tag type="info">{{ seedLabel }}</el-tag>
          <el-tag type="info">Nodes: {{ stats.nodes }}</el-tag>
          <el-tag type="info">Edges: {{ stats.edges }}</el-tag>
          <el-tag v-if="stats.bottlenecks" type="danger">Bottlenecks: {{ stats.bottlenecks }}</el-tag>
        </div>
      </div>
    </template>

    <el-alert v-if="error" :title="error" type="error" show-icon class="mb" />

    <div class="toolbar">
      <el-tabs v-model="toolbarTab" type="card" class="toolbarTabs">
        <el-tab-pane label="Filters" name="filters">
          <div class="paneGrid">
            <div class="ctl ctl--eq">
              <TooltipLabel class="toolbarLabel ctl__label" label="Equivalent" tooltip-key="graph.eq" />
              <el-select v-model="eq" size="small" class="ctl__field">
                <el-option v-for="c in availableEquivalents" :key="c" :label="c" :value="c" />
              </el-select>
            </div>

            <div class="ctl ctl--status">
              <TooltipLabel class="toolbarLabel ctl__label" label="Status" tooltip-key="graph.status" />
              <el-select v-model="statusFilter" multiple collapse-tags collapse-tags-tooltip size="small" class="ctl__field">
                <el-option v-for="s in statuses" :key="s.value" :label="s.label" :value="s.value" />
              </el-select>
            </div>

            <div class="ctl ctl--threshold">
              <TooltipLabel class="toolbarLabel ctl__label" label="Bottleneck" tooltip-key="graph.threshold" />
              <el-input v-model="threshold" size="small" class="ctl__field" placeholder="0.10" />
            </div>

            <div class="ctl">
              <TooltipLabel class="toolbarLabel ctl__label" label="Type" tooltip-key="graph.type" />
              <el-checkbox-group v-model="typeFilter" size="small">
                <el-checkbox-button label="person">person</el-checkbox-button>
                <el-checkbox-button label="business">business</el-checkbox-button>
              </el-checkbox-group>
            </div>

            <div class="ctl ctl--degree">
              <TooltipLabel class="toolbarLabel ctl__label" label="Min degree" tooltip-key="graph.minDegree" />
              <el-input-number v-model="minDegree" size="small" :min="0" :max="20" controls-position="right" class="ctl__field" />
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="Display" name="display">
          <div class="paneGrid">
            <div class="ctl ctl--layout">
              <TooltipLabel class="toolbarLabel ctl__label" label="Layout" tooltip-key="graph.layout" />
              <el-select v-model="layoutName" size="small" class="ctl__field">
                <el-option v-for="o in layoutOptions" :key="o.value" :label="o.label" :value="o.value" />
              </el-select>
            </div>

            <div class="ctl">
              <TooltipLabel class="toolbarLabel ctl__label" label="Layout spacing" tooltip-key="graph.spacing" />
              <el-slider v-model="layoutSpacing" :min="1" :max="3" :step="0.1" class="sliderField" />
            </div>

            <div class="ctl">
              <TooltipLabel class="toolbarLabel ctl__label" label="Business labels" tooltip-key="graph.labels" />
              <el-checkbox-group v-model="businessLabelParts" size="small">
                <el-checkbox-button label="name">name</el-checkbox-button>
                <el-checkbox-button label="pid">pid</el-checkbox-button>
              </el-checkbox-group>
            </div>

            <div class="ctl">
              <TooltipLabel class="toolbarLabel ctl__label" label="Person labels" tooltip-key="graph.labels" />
              <el-checkbox-group v-model="personLabelParts" size="small">
                <el-checkbox-button label="name">name</el-checkbox-button>
                <el-checkbox-button label="pid">pid</el-checkbox-button>
              </el-checkbox-group>
            </div>

            <div class="toggleGrid">
              <div class="toggleLine">
                <TooltipLabel class="toolbarLabel" label="Labels" tooltip-key="graph.labels" />
                <el-switch v-model="showLabels" size="small" />
              </div>
              <div class="toggleLine">
                <TooltipLabel class="toolbarLabel" label="Auto labels" tooltip-key="graph.labels" />
                <el-switch v-model="autoLabelsByZoom" size="small" />
              </div>
              <div class="toggleLine">
                <TooltipLabel class="toolbarLabel" label="Incidents" tooltip-key="graph.incidents" />
                <el-switch v-model="showIncidents" size="small" />
              </div>
              <div class="toggleLine">
                <TooltipLabel class="toolbarLabel" label="Hide isolates" tooltip-key="graph.hideIsolates" />
                <el-switch v-model="hideIsolates" size="small" />
              </div>
              <div class="toggleLine">
                <TooltipLabel class="toolbarLabel" label="Legend" tooltip-key="graph.legend" />
                <el-switch v-model="showLegend" size="small" />
              </div>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="Navigate" name="navigate">
          <div class="navPane">
            <div class="navRow navRow--search">
              <TooltipLabel class="toolbarLabel navRow__label" label="Search" tooltip-key="graph.search" />
              <el-autocomplete
                v-model="searchQuery"
                :fetch-suggestions="querySearchParticipants"
                placeholder="Type PID or name…"
                size="small"
                clearable
                class="navRow__field"
                @select="onSearchSelect"
                @keyup.enter="focusSearch"
              />
            </div>

            <div class="navRow navRow--actions">
              <TooltipLabel class="toolbarLabel navRow__label" label="Actions" tooltip-key="graph.actions" />
              <div class="navActions">
                <el-button size="small" :disabled="!canFind" @click="focusSearch">Find</el-button>
                <el-button size="small" @click="fit">Fit</el-button>
                <el-button size="small" @click="runLayout">Re-layout</el-button>

                <div class="zoomrow">
                  <TooltipLabel class="toolbarLabel zoomrow__label" label="Zoom" tooltip-key="graph.zoom" />
                  <el-slider v-model="zoom" :min="0.1" :max="3" :step="0.05" class="zoomrow__slider" />
                </div>
              </div>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <el-skeleton v-if="loading" animated :rows="6" />

    <div v-else class="cy-wrap">
      <div v-if="showLegend" class="legend">
        <div class="legend__title">Legend</div>
        <div class="legend__row">
          <span class="swatch swatch--node-active" /> active participant
        </div>
        <div class="legend__row">
          <span class="swatch swatch--node-frozen" /> frozen participant
        </div>
        <div class="legend__row">
          <span class="swatch swatch--node-business" /> business (node shape)
        </div>
        <div class="legend__row">
          <span class="swatch swatch--edge-active" /> active trustline
        </div>
        <div class="legend__row">
          <span class="swatch swatch--edge-frozen" /> frozen trustline
        </div>
        <div class="legend__row">
          <span class="swatch swatch--edge-closed" /> closed trustline
        </div>
        <div class="legend__row">
          <span class="swatch swatch--edge-bottleneck" /> bottleneck (thick)
        </div>
        <div class="legend__row">
          <span class="swatch swatch--edge-incident" /> incident initiator side (dashed)
        </div>
      </div>
      <div ref="cyRoot" class="cy" />
    </div>
  </el-card>

  <el-drawer v-model="drawerOpen" title="Details" size="40%">
    <div v-if="selected && selected.kind === 'node'">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="PID">{{ selected.pid }}</el-descriptions-item>
        <el-descriptions-item label="Display name">{{ selected.display_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="Status">{{ selected.status || '-' }}</el-descriptions-item>
        <el-descriptions-item label="Type">{{ selected.type || '-' }}</el-descriptions-item>
        <el-descriptions-item label="Degree">{{ selected.degree }}</el-descriptions-item>
        <el-descriptions-item label="In / out">{{ selected.inDegree }} / {{ selected.outDegree }}</el-descriptions-item>
        <el-descriptions-item v-if="showIncidents" label="Incident ratio">
          {{ (incidentRatioByPid.get(selected.pid) || 0).toFixed(2) }}
        </el-descriptions-item>
      </el-descriptions>

      <el-divider>Analytics (fixtures-first)</el-divider>

      <div class="drawerControls">
        <div class="drawerControls__row">
          <div class="ctl">
            <div class="toolbarLabel">Equivalent</div>
            <el-select v-model="drawerEq" size="small" filterable class="ctl__field" placeholder="Equivalent">
              <el-option v-for="o in availableEquivalents" :key="o" :label="o" :value="o" />
            </el-select>
          </div>
          <div class="drawerControls__actions">
            <el-button size="small" @click="loadData">Refresh</el-button>
          </div>
        </div>
      </div>

      <el-tabs v-model="drawerTab" class="drawerTabs">
        <el-tab-pane label="Summary" name="summary">
          <el-alert v-if="!analyticsEq" title="Pick an equivalent (not ALL) for full analytics." type="info" show-icon class="mb" />

          <div class="hint">Fixtures-first: derived from trustlines + debts + incidents + audit-log.</div>

          <!-- TODO(ui): toggles blocks are duplicated across Summary/Balance/Risk tabs.
               Extract a shared WidgetTogglesCard (or a declarative config list) to avoid drift in text/disabled logic. -->
          <el-card v-if="analyticsEq" shadow="never" class="mb">
            <template #header>
              <TooltipLabel
                label="Summary widgets"
                tooltip-text="Show/hide summary cards. These toggles are stored in localStorage for this browser."
              />
            </template>
            <div class="toggleGrid">
              <div class="toggleLine">
                <el-switch v-model="analytics.showRank" size="small" :disabled="!analyticsEq" />
                <TooltipLabel
                  label="Rank / percentile"
                  tooltip-text="Your position among all participants by net balance for the selected equivalent."
                />
              </div>
              <div class="toggleLine">
                <el-switch v-model="analytics.showConcentration" size="small" :disabled="!analyticsEq" />
                <TooltipLabel
                  label="Concentration risk"
                  tooltip-text="How concentrated your debts/credits are across counterparties (top1/top5 shares + HHI)."
                />
              </div>
              <div class="toggleLine">
                <el-switch v-model="analytics.showCapacity" size="small" :disabled="!analyticsEq" />
                <TooltipLabel
                  label="Trustline capacity"
                  tooltip-text="Aggregate trustline capacity around the participant: used% = total_used / total_limit."
                />
              </div>
              <div class="toggleLine">
                <el-switch v-model="analytics.showActivity" size="small" :disabled="!analyticsEq" />
                <TooltipLabel
                  label="Activity / churn"
                  tooltip-text="Recent changes around the participant in rolling windows (7/30/90 days)."
                />
              </div>
            </div>
          </el-card>

          <div v-if="analyticsEq" class="summaryGrid">
            <el-card shadow="never" class="summaryCard">
              <template #header>
                <TooltipLabel
                  label="Net position"
                  tooltip-text="Net balance in the selected equivalent: total_credit − total_debt (derived from debts fixture)."
                />
              </template>
              <div v-if="selectedRank" class="kpi">
                <div class="kpi__value">{{ money(selectedRank.net) }} {{ selectedRank.eq }}</div>
                <div class="kpi__hint muted">credit − debt</div>
              </div>
              <div v-else class="muted">No data</div>
            </el-card>

            <el-card v-if="analytics.showRank" shadow="never" class="summaryCard">
              <template #header>
                <TooltipLabel
                  label="Rank / percentile"
                  tooltip-text="Your position among all participants by net balance for the selected equivalent (1 = top net creditor)."
                />
              </template>
              <div v-if="selectedRank" class="kpi">
                <div class="kpi__value">rank {{ selectedRank.rank }}/{{ selectedRank.n }}</div>
                <el-progress :percentage="Math.round((selectedRank.percentile || 0) * 100)" :stroke-width="10" :show-text="false" />
                <div class="kpi__hint muted">Percentile: {{ pct(selectedRank.percentile, 0) }}</div>
              </div>
              <div v-else class="muted">No data</div>
            </el-card>

            <el-card v-if="analytics.showConcentration" shadow="never" class="summaryCard">
              <template #header>
                <TooltipLabel
                  label="Concentration"
                  tooltip-text="How concentrated your debts/credits are across counterparties (top1/top5 shares + HHI). Higher = more dependence on a few counterparties."
                />
              </template>
              <div v-if="selectedConcentration.eq" class="kpi">
                <div class="kpi__row">
                  <span class="geoLabel">Outgoing (you owe)</span>
                  <el-tag :type="selectedConcentration.outgoing.level.type" size="small">{{ selectedConcentration.outgoing.level.label }}</el-tag>
                </div>
                <div class="metricRows">
                  <div class="metricRow">
                    <TooltipLabel class="metricRow__label" label="Top1 share" tooltip-text="Share of total outgoing debt owed to the largest single creditor." />
                    <span class="metricRow__value">{{ pct(selectedConcentration.outgoing.top1, 0) }}</span>
                  </div>
                  <div class="metricRow">
                    <TooltipLabel class="metricRow__label" label="Top5 share" tooltip-text="Share of total outgoing debt owed to the largest 5 creditors combined." />
                    <span class="metricRow__value">{{ pct(selectedConcentration.outgoing.top5, 0) }}</span>
                  </div>
                  <div class="metricRow">
                    <TooltipLabel
                      class="metricRow__label"
                      label="HHI"
                      tooltip-text="Herfindahl–Hirschman Index = sum of squared counterparty shares. Closer to 1 means more concentrated."
                    />
                    <span class="metricRow__value">{{ selectedConcentration.outgoing.hhi.toFixed(2) }}</span>
                  </div>
                </div>
                <div class="kpi__row" style="margin-top: 10px">
                  <span class="geoLabel">Incoming (owed to you)</span>
                  <el-tag :type="selectedConcentration.incoming.level.type" size="small">{{ selectedConcentration.incoming.level.label }}</el-tag>
                </div>
                <div class="metricRows">
                  <div class="metricRow">
                    <TooltipLabel class="metricRow__label" label="Top1 share" tooltip-text="Share of total incoming credit owed by the largest single debtor." />
                    <span class="metricRow__value">{{ pct(selectedConcentration.incoming.top1, 0) }}</span>
                  </div>
                  <div class="metricRow">
                    <TooltipLabel class="metricRow__label" label="Top5 share" tooltip-text="Share of total incoming credit owed by the largest 5 debtors combined." />
                    <span class="metricRow__value">{{ pct(selectedConcentration.incoming.top5, 0) }}</span>
                  </div>
                  <div class="metricRow">
                    <TooltipLabel
                      class="metricRow__label"
                      label="HHI"
                      tooltip-text="Herfindahl–Hirschman Index = sum of squared counterparty shares. Closer to 1 means more concentrated."
                    />
                    <span class="metricRow__value">{{ selectedConcentration.incoming.hhi.toFixed(2) }}</span>
                  </div>
                </div>
              </div>
              <div v-else class="muted">No data</div>
            </el-card>

            <el-card v-if="analytics.showCapacity" shadow="never" class="summaryCard">
              <template #header>
                <TooltipLabel
                  label="Capacity"
                  tooltip-text="Aggregate trustline capacity around the participant: used% = total_used / total_limit (incoming/outgoing)."
                />
              </template>
              <div v-if="selectedCapacity" class="kpi">
                <div class="kpi__row">
                  <span class="muted">Outgoing used</span>
                  <span class="kpi__metric">{{ pct(selectedCapacity.out.pct, 0) }}</span>
                </div>
                <el-progress :percentage="Math.round((selectedCapacity.out.pct || 0) * 100)" :stroke-width="10" :show-text="false" />
                <div class="kpi__row" style="margin-top: 10px">
                  <span class="muted">Incoming used</span>
                  <span class="kpi__metric">{{ pct(selectedCapacity.inc.pct, 0) }}</span>
                </div>
                <el-progress :percentage="Math.round((selectedCapacity.inc.pct || 0) * 100)" :stroke-width="10" :show-text="false" />
                <div v-if="analytics.showBottlenecks" class="kpi__hint muted" style="margin-top: 8px">
                  Bottlenecks: {{ selectedCapacity.bottlenecks.length }} (threshold {{ threshold }})
                </div>
              </div>
              <div v-else class="muted">No data</div>
            </el-card>

            <el-card v-if="analytics.showActivity" shadow="never" class="summaryCard">
              <template #header>
                <TooltipLabel
                  label="Activity / churn"
                  tooltip-text="Recent changes around the participant in rolling windows (7/30/90 days), based on fixture timestamps."
                />
              </template>
              <div v-if="selectedActivity" class="metricRows">
                <div class="metricRow">
                  <TooltipLabel
                    class="metricRow__label"
                    label="Trustlines created (7/30/90d)"
                    tooltip-text="Count of trustlines involving this participant with created_at inside each window."
                  />
                  <span class="metricRow__value">{{ selectedActivity.trustlineCreated[7] }} / {{ selectedActivity.trustlineCreated[30] }} / {{ selectedActivity.trustlineCreated[90] }}</span>
                </div>
                <div class="metricRow">
                  <TooltipLabel
                    class="metricRow__label"
                    label="Trustlines closed now (7/30/90d)"
                    tooltip-text="Not an event counter: among trustlines created inside each window, how many are currently status=closed."
                  />
                  <span class="metricRow__value">{{ selectedActivity.trustlineClosed[7] }} / {{ selectedActivity.trustlineClosed[30] }} / {{ selectedActivity.trustlineClosed[90] }}</span>
                </div>
                <div class="metricRow">
                  <TooltipLabel
                    class="metricRow__label"
                    label="Incidents (initiator, 7/30/90d)"
                    tooltip-text="Count of incident records where this participant is the initiator_pid, by created_at window."
                  />
                  <span class="metricRow__value">{{ selectedActivity.incidentCount[7] }} / {{ selectedActivity.incidentCount[30] }} / {{ selectedActivity.incidentCount[90] }}</span>
                </div>
                <div class="metricRow">
                  <TooltipLabel
                    class="metricRow__label"
                    label="Participant ops (audit-log, 7/30/90d)"
                    tooltip-text="Count of audit-log actions starting with PARTICIPANT_* for this pid (object_id), by timestamp window."
                  />
                  <span class="metricRow__value">{{ selectedActivity.participantOps[7] }} / {{ selectedActivity.participantOps[30] }} / {{ selectedActivity.participantOps[90] }}</span>
                </div>
              </div>
              <div v-else class="muted">No data</div>
            </el-card>
          </div>
        </el-tab-pane>

        <el-tab-pane label="Balance" name="balance">
          <div class="hint">Derived from trustlines + debts fixtures (debts are derived from trustline.used).</div>

          <el-alert
            v-if="!analyticsEq"
            title="Pick an equivalent (not ALL) to enable analytics cards"
            description="With ALL selected, the Balance table still works, but Rank/Distribution/Counterparty/Risk visualizations are hidden because they are per-equivalent."
            type="info"
            show-icon
            class="mb"
          />

          <el-card v-if="analyticsEq" shadow="never" class="mb">
            <template #header>
              <TooltipLabel
                label="Balance widgets"
                tooltip-text="Show/hide balance visualizations. These toggles are stored in localStorage for this browser."
              />
            </template>
            <div class="toggleGrid">
              <div class="toggleLine">
                <el-switch v-model="analytics.showRank" size="small" :disabled="!analyticsEq" />
                <TooltipLabel
                  label="Rank / percentile"
                  tooltip-text="Rank/percentile of net balance across all participants (for selected equivalent)."
                />
              </div>
              <div class="toggleLine">
                <el-switch v-model="analytics.showDistribution" size="small" :disabled="!analyticsEq" />
                <TooltipLabel
                  label="Distribution histogram"
                  tooltip-text="Tiny histogram of net balance distribution across all participants (selected equivalent)."
                />
              </div>
            </div>
          </el-card>

          <el-card v-if="analytics.showRank && selectedRank" shadow="never" class="mb">
            <template #header>
              <TooltipLabel
                :label="`Rank / percentile (${selectedRank.eq})`"
                tooltip-text="Rank is 1..N by net balance; percentile is normalized to 0..100 where 100% is the top net creditor."
              />
            </template>
            <div class="kpi">
              <div class="kpi__value">rank {{ selectedRank.rank }}/{{ selectedRank.n }}</div>
              <el-progress :percentage="Math.round((selectedRank.percentile || 0) * 100)" :stroke-width="10" :show-text="false" />
              <div class="kpi__hint muted">Percentile: {{ pct(selectedRank.percentile, 0) }}</div>
            </div>
          </el-card>

          <el-card v-if="analytics.showDistribution && netDistribution" shadow="never" class="mb">
            <template #header>
              <TooltipLabel
                :label="`Distribution (${netDistribution.eq})`"
                tooltip-text="Histogram of net balances across all participants for the selected equivalent."
              />
            </template>
            <div class="hist">
              <div
                v-for="(b, i) in netDistribution.bins"
                :key="i"
                class="hist__bar"
                :style="{ height: ((b.count / Math.max(1, Math.max(...netDistribution.bins.map(x => x.count)))) * 48).toFixed(0) + 'px' }"
                :title="String(b.count)"
              />
            </div>
            <div class="hist__labels muted">
              <span>{{ money(atomsToDecimal(netDistribution.min, precisionByEq.get(netDistribution.eq) ?? 2)) }}</span>
              <span>{{ money(atomsToDecimal(netDistribution.max, precisionByEq.get(netDistribution.eq) ?? 2)) }}</span>
            </div>
          </el-card>

          <el-empty v-if="selectedBalanceRows.length === 0" description="No data" />
          <el-table v-else :data="selectedBalanceRows" size="small" class="geoTable">
            <el-table-column prop="equivalent" label="Equivalent" width="120" />
            <el-table-column prop="outgoing_limit" label="Out limit" min-width="120">
              <template #default="{ row }">{{ money(row.outgoing_limit) }}</template>
            </el-table-column>
            <el-table-column prop="outgoing_used" label="Out used" min-width="120">
              <template #default="{ row }">{{ money(row.outgoing_used) }}</template>
            </el-table-column>
            <el-table-column prop="incoming_limit" label="In limit" min-width="120">
              <template #default="{ row }">{{ money(row.incoming_limit) }}</template>
            </el-table-column>
            <el-table-column prop="incoming_used" label="In used" min-width="120">
              <template #default="{ row }">{{ money(row.incoming_used) }}</template>
            </el-table-column>
            <el-table-column prop="total_debt" label="Debt" min-width="120">
              <template #default="{ row }">{{ money(row.total_debt) }}</template>
            </el-table-column>
            <el-table-column prop="total_credit" label="Credit" min-width="120">
              <template #default="{ row }">{{ money(row.total_credit) }}</template>
            </el-table-column>
            <el-table-column prop="net" label="Net" min-width="120">
              <template #default="{ row }">{{ money(row.net) }}</template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="Counterparties" name="counterparties">
          <el-alert v-if="!analyticsEq" title="Pick an equivalent (not ALL) to inspect counterparties." type="info" show-icon class="mb" />

          <div v-else>
            <div class="splitGrid">
              <el-card shadow="never">
                <template #header>
                  <TooltipLabel
                    label="Top creditors (you owe)"
                    tooltip-text="Participants who are creditors of this participant (debts where you are the debtor)."
                  />
                </template>
                <el-empty v-if="selectedCounterpartySplit.creditors.length === 0" description="No creditors" />
                <el-table v-else :data="selectedCounterpartySplit.creditors.slice(0, 10)" size="small" class="geoTable">
                  <el-table-column prop="display_name" label="Participant" min-width="220" />
                  <el-table-column prop="amount" label="Amount" min-width="120">
                    <template #default="{ row }">{{ money(row.amount) }}</template>
                  </el-table-column>
                  <el-table-column prop="share" label="Share" min-width="140">
                    <template #default="{ row }">
                      <div class="shareCell">
                        <el-progress :percentage="Math.round((row.share || 0) * 100)" :stroke-width="10" :show-text="false" />
                        <span class="muted">{{ pct(row.share, 0) }}</span>
                      </div>
                    </template>
                  </el-table-column>
                </el-table>
              </el-card>

              <el-card shadow="never">
                <template #header>
                  <TooltipLabel
                    label="Top debtors (owed to you)"
                    tooltip-text="Participants who are debtors to this participant (debts where you are the creditor)."
                  />
                </template>
                <el-empty v-if="selectedCounterpartySplit.debtors.length === 0" description="No debtors" />
                <el-table v-else :data="selectedCounterpartySplit.debtors.slice(0, 10)" size="small" class="geoTable">
                  <el-table-column prop="display_name" label="Participant" min-width="220" />
                  <el-table-column prop="amount" label="Amount" min-width="120">
                    <template #default="{ row }">{{ money(row.amount) }}</template>
                  </el-table-column>
                  <el-table-column prop="share" label="Share" min-width="140">
                    <template #default="{ row }">
                      <div class="shareCell">
                        <el-progress :percentage="Math.round((row.share || 0) * 100)" :stroke-width="10" :show-text="false" />
                        <span class="muted">{{ pct(row.share, 0) }}</span>
                      </div>
                    </template>
                  </el-table-column>
                </el-table>
              </el-card>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="Risk" name="risk">
          <el-alert v-if="!analyticsEq" title="Pick an equivalent (not ALL) to inspect risk metrics." type="info" show-icon class="mb" />

          <el-card v-if="analyticsEq" shadow="never" class="mb">
            <template #header>
              <TooltipLabel
                label="Risk widgets"
                tooltip-text="Show/hide risk-related widgets in this tab. These toggles are stored in localStorage for this browser."
              />
            </template>
            <div class="toggleGrid">
              <div class="toggleLine">
                <el-switch v-model="analytics.showConcentration" size="small" :disabled="!analyticsEq" />
                <TooltipLabel label="Concentration risk" tooltip-text="Top1/top5 shares and HHI derived from counterparty debt shares." />
              </div>
              <div class="toggleLine">
                <el-switch v-model="analytics.showCapacity" size="small" :disabled="!analyticsEq" />
                <TooltipLabel label="Trustline capacity" tooltip-text="Aggregate incoming/outgoing used vs limit and bottleneck detection." />
              </div>
              <div class="toggleLine">
                <el-switch v-model="analytics.showBottlenecks" size="small" :disabled="!analyticsEq || !analytics.showCapacity" />
                <TooltipLabel label="Bottlenecks" tooltip-text="Show bottleneck count/list inside Trustline capacity (threshold-based)." />
              </div>
              <div class="toggleLine">
                <el-switch v-model="analytics.showActivity" size="small" :disabled="!analyticsEq" />
                <TooltipLabel label="Activity / churn" tooltip-text="7/30/90-day counts from fixtures timestamps (trustlines/incidents/audit/transactions)." />
              </div>
            </div>
          </el-card>

          <el-card v-if="analyticsEq && analytics.showConcentration" shadow="never" class="mb">
            <template #header>
              <TooltipLabel
                :label="`Concentration (${analyticsEq})`"
                tooltip-text="Counterparty concentration risk derived from debt shares: top1/top5 and HHI."
              />
            </template>
            <div v-if="selectedConcentration.eq" class="riskGrid">
              <div class="riskBlock">
                <div class="riskBlock__hdr">
                  <TooltipLabel
                    label="Outgoing concentration"
                    tooltip-text="How concentrated your outgoing debts are (you owe). Higher = dependence on fewer creditors."
                  />
                  <el-tag :type="selectedConcentration.outgoing.level.type" size="small">{{ selectedConcentration.outgoing.level.label }}</el-tag>
                </div>
                <div class="metricRows">
                  <div class="metricRow">
                    <TooltipLabel class="metricRow__label" label="Top1 share" tooltip-text="Share of total outgoing debt owed to the largest single creditor." />
                    <span class="metricRow__value">{{ pct(selectedConcentration.outgoing.top1, 0) }}</span>
                  </div>
                  <div class="metricRow">
                    <TooltipLabel class="metricRow__label" label="Top5 share" tooltip-text="Share of total outgoing debt owed to the largest 5 creditors combined." />
                    <span class="metricRow__value">{{ pct(selectedConcentration.outgoing.top5, 0) }}</span>
                  </div>
                  <div class="metricRow">
                    <TooltipLabel
                      class="metricRow__label"
                      label="HHI"
                      tooltip-text="Herfindahl–Hirschman Index = sum of squared counterparty shares. Closer to 1 means more concentrated."
                    />
                    <span class="metricRow__value">{{ selectedConcentration.outgoing.hhi.toFixed(2) }}</span>
                  </div>
                </div>
              </div>
              <div class="riskBlock">
                <div class="riskBlock__hdr">
                  <TooltipLabel
                    label="Incoming concentration"
                    tooltip-text="How concentrated your incoming credits are (owed to you). Higher = dependence on fewer debtors."
                  />
                  <el-tag :type="selectedConcentration.incoming.level.type" size="small">{{ selectedConcentration.incoming.level.label }}</el-tag>
                </div>
                <div class="metricRows">
                  <div class="metricRow">
                    <TooltipLabel class="metricRow__label" label="Top1 share" tooltip-text="Share of total incoming credit owed by the largest single debtor." />
                    <span class="metricRow__value">{{ pct(selectedConcentration.incoming.top1, 0) }}</span>
                  </div>
                  <div class="metricRow">
                    <TooltipLabel class="metricRow__label" label="Top5 share" tooltip-text="Share of total incoming credit owed by the largest 5 debtors combined." />
                    <span class="metricRow__value">{{ pct(selectedConcentration.incoming.top5, 0) }}</span>
                  </div>
                  <div class="metricRow">
                    <TooltipLabel
                      class="metricRow__label"
                      label="HHI"
                      tooltip-text="Herfindahl–Hirschman Index = sum of squared counterparty shares. Closer to 1 means more concentrated."
                    />
                    <span class="metricRow__value">{{ selectedConcentration.incoming.hhi.toFixed(2) }}</span>
                  </div>
                </div>
              </div>
            </div>
            <div v-else class="muted">No data</div>
          </el-card>

          <el-card v-if="analyticsEq && analytics.showCapacity && selectedCapacity" shadow="never" class="mb">
            <template #header>
              <TooltipLabel
                label="Trustline capacity"
                tooltip-text="How much of the participant’s trustline limits are already used. Outgoing = used on lines where participant is creditor; incoming = used on lines where participant is debtor."
              />
            </template>
            <div class="capRow">
              <div class="capRow__label">
                <TooltipLabel
                  label="Outgoing used"
                  tooltip-text="Used / limit aggregated across all outgoing trustlines (participant is creditor)."
                />
              </div>
              <el-progress :percentage="Math.round((selectedCapacity.out.pct || 0) * 100)" :stroke-width="10" :show-text="false" />
              <div class="capRow__value">{{ pct(selectedCapacity.out.pct, 0) }}</div>
            </div>
            <div class="capRow">
              <div class="capRow__label">
                <TooltipLabel
                  label="Incoming used"
                  tooltip-text="Used / limit aggregated across all incoming trustlines (participant is debtor)."
                />
              </div>
              <el-progress :percentage="Math.round((selectedCapacity.inc.pct || 0) * 100)" :stroke-width="10" :show-text="false" />
              <div class="capRow__value">{{ pct(selectedCapacity.inc.pct, 0) }}</div>
            </div>
            <div v-if="analytics.showBottlenecks" class="mb" style="margin-top: 10px">
              <el-tag type="info" size="small">
                <TooltipLabel
                  :label="`Bottlenecks: ${selectedCapacity.bottlenecks.length}`"
                  tooltip-text="Trustlines where available/limit is below the selected threshold."
                />
              </el-tag>
              <span class="muted" style="margin-left: 8px">threshold {{ threshold }}</span>
            </div>
            <el-collapse v-if="analytics.showBottlenecks && selectedCapacity.bottlenecks.length" accordion>
              <el-collapse-item name="bottlenecks">
                <template #title>
                  <TooltipLabel
                    label="Bottlenecks list"
                    tooltip-text="List of trustlines close to saturation (low available relative to limit)."
                  />
                </template>
                <el-table :data="selectedCapacity.bottlenecks" size="small" class="geoTable">
                  <el-table-column prop="dir" label="Dir" width="70" />
                  <el-table-column prop="other" label="Counterparty" min-width="220" />
                  <el-table-column label="Limit / Used / Avail" min-width="220">
                    <template #default="{ row }">
                      {{ money(row.t.limit) }} / {{ money(row.t.used) }} / {{ money(row.t.available) }}
                    </template>
                  </el-table-column>
                </el-table>
              </el-collapse-item>
            </el-collapse>
          </el-card>

          <el-card v-if="analyticsEq && analytics.showActivity && selectedActivity" shadow="never">
            <template #header>
              <TooltipLabel
                label="Activity / churn"
                tooltip-text="Counts by 7/30/90-day windows. Sources: trustlines.created_at, incidents.created_at, audit-log.timestamp, transactions.updated_at."
              />
            </template>
            <div class="metricRows">
              <div class="metricRow">
                <TooltipLabel
                  class="metricRow__label"
                  label="Trustlines created (7/30/90d)"
                  tooltip-text="Count of trustlines involving this participant with created_at inside each window."
                />
                <span class="metricRow__value">{{ selectedActivity.trustlineCreated[7] }} / {{ selectedActivity.trustlineCreated[30] }} / {{ selectedActivity.trustlineCreated[90] }}</span>
              </div>
              <div class="metricRow">
                <TooltipLabel
                  class="metricRow__label"
                  label="Trustlines closed now (7/30/90d)"
                  tooltip-text="Not an event counter: among trustlines created inside each window, how many are currently status=closed."
                />
                <span class="metricRow__value">{{ selectedActivity.trustlineClosed[7] }} / {{ selectedActivity.trustlineClosed[30] }} / {{ selectedActivity.trustlineClosed[90] }}</span>
              </div>
              <div class="metricRow">
                <TooltipLabel
                  class="metricRow__label"
                  label="Incidents (initiator, 7/30/90d)"
                  tooltip-text="Count of incident records where this participant is the initiator_pid, by created_at window."
                />
                <span class="metricRow__value">{{ selectedActivity.incidentCount[7] }} / {{ selectedActivity.incidentCount[30] }} / {{ selectedActivity.incidentCount[90] }}</span>
              </div>
              <div class="metricRow">
                <TooltipLabel
                  class="metricRow__label"
                  label="Participant ops (audit-log, 7/30/90d)"
                  tooltip-text="Count of audit-log actions starting with PARTICIPANT_* for this pid (object_id), by timestamp window."
                />
                <span class="metricRow__value">{{ selectedActivity.participantOps[7] }} / {{ selectedActivity.participantOps[30] }} / {{ selectedActivity.participantOps[90] }}</span>
              </div>
              <div class="metricRow">
                <TooltipLabel
                  class="metricRow__label"
                  label="Payments committed (7/30/90d)"
                  tooltip-text="Count of committed PAYMENT transactions involving this participant (as sender or receiver), by updated_at window."
                />
                <span class="metricRow__value">{{ selectedActivity.paymentCommitted[7] }} / {{ selectedActivity.paymentCommitted[30] }} / {{ selectedActivity.paymentCommitted[90] }}</span>
              </div>
              <div class="metricRow">
                <TooltipLabel
                  class="metricRow__label"
                  label="Clearing committed (7/30/90d)"
                  tooltip-text="Count of committed CLEARING transactions where this participant appears in any cycle edge (or is initiator), by updated_at window."
                />
                <span class="metricRow__value">{{ selectedActivity.clearingCommitted[7] }} / {{ selectedActivity.clearingCommitted[30] }} / {{ selectedActivity.clearingCommitted[90] }}</span>
              </div>
            </div>
            <el-alert
              v-if="!selectedActivity.hasTransactions"
              type="warning"
              show-icon
              title="Transactions-based activity is unavailable in fixtures"
              description="Missing datasets/transactions.json in the current seed. In real mode this must come from API."
              class="mb"
              style="margin-top: 10px"
            />
          </el-card>
        </el-tab-pane>

        <el-tab-pane label="Cycles" name="cycles">
          <el-alert v-if="!analyticsEq" title="Pick an equivalent (not ALL) to inspect clearing cycles." type="info" show-icon class="mb" />
          <el-empty v-else-if="selectedCycles.length === 0" description="No cycles found in fixtures" />
          <div v-else class="cycles">
            <div class="hint">
              <TooltipLabel
                label="Clearing cycles"
                tooltip-text="Each cycle is a directed loop in the debt graph for the selected equivalent. Clearing reduces each edge by the shown amount."
              />
            </div>
            <div v-for="(c, idx) in selectedCycles" :key="idx" class="cycleItem">
              <div class="cycleTitle">
                <TooltipLabel
                  :label="`Cycle #${idx + 1}`"
                  tooltip-text="A cycle is a set of debts that can be cleared together while preserving net positions (cycle cancelation)."
                />
              </div>
              <div v-for="(e, j) in c" :key="j" class="cycleEdge">
                <span class="mono">{{ e.debtor }}</span>
                <span class="muted">→</span>
                <span class="mono">{{ e.creditor }}</span>
                <span class="muted">({{ e.equivalent }} {{ money(e.amount) }})</span>
              </div>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <div v-else-if="selected && selected.kind === 'edge'">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="Equivalent">{{ selected.equivalent }}</el-descriptions-item>
        <el-descriptions-item label="From">{{ selected.from }}</el-descriptions-item>
        <el-descriptions-item label="To">{{ selected.to }}</el-descriptions-item>
        <el-descriptions-item label="Status">{{ selected.status }}</el-descriptions-item>
        <el-descriptions-item label="Limit">{{ money(selected.limit) }}</el-descriptions-item>
        <el-descriptions-item label="Used">{{ money(selected.used) }}</el-descriptions-item>
        <el-descriptions-item label="Available">{{ money(selected.available) }}</el-descriptions-item>
        <el-descriptions-item label="Created at">{{ selected.created_at }}</el-descriptions-item>
      </el-descriptions>
    </div>
  </el-drawer>
</template>

<style scoped>
.mb {
  margin-bottom: 12px;
}

.hdr {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.hdr__right {
  display: flex;
  gap: 8px;
  align-items: center;
}

.toolbar {
  margin-bottom: 12px;
}


.toolbarTabs :deep(.el-tabs__header) {
  margin: 0 0 8px 0;
}

.toolbarTabs :deep(.el-tabs__content) {
  padding: 0;
}

.drawerControls {
  margin-bottom: 10px;
}

.drawerControls__row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 10px;
  align-items: end;
}

.drawerControls__actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.drawerTabs :deep(.el-tabs__header) {
  margin: 0 0 8px 0;
}

.drawerTabs :deep(.el-tabs__content) {
  padding: 0;
}

.drawerTabs {
  font-size: var(--geo-font-size-label);
  line-height: 1.35;
}

/* Typography roles inside the analytics drawer */
.drawerTabs :deep(.el-card__header) {
  font-size: var(--geo-font-size-title);
  font-weight: var(--geo-font-weight-title);
  color: var(--el-text-color-primary);
}

.drawerTabs :deep(.el-table__header .cell) {
  font-weight: var(--geo-font-weight-table-header);
  color: var(--el-text-color-primary);
}

.hint {
  margin-bottom: 10px;
  font-size: var(--geo-font-size-label);
  color: var(--el-text-color-secondary);
}

.summaryGrid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
}

.summaryCard :deep(.el-card__header) {
  padding: 10px 12px;
}

.summaryCard :deep(.el-card__body) {
  padding: 12px;
}

.kpi {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.kpi__value {
  font-size: var(--geo-font-size-value);
  font-weight: var(--geo-font-weight-value);
}

.kpi__hint {
  font-size: var(--geo-font-size-sub);
}

.kpi__metric {
  font-size: var(--geo-font-size-label);
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.metricRows {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.metricRow {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.metricRow__label {
  min-width: 0;
  flex: 1;
  color: var(--el-text-color-secondary);
}

.metricRow__value {
  flex: 0 0 auto;
  font-size: var(--geo-font-size-label);
  font-weight: 600;
  color: var(--el-text-color-primary);
  text-align: right;
}

.kpi__row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}

.splitGrid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
}

.shareCell {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
  align-items: center;
}

.riskGrid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
}

@media (min-width: 780px) {
  .riskGrid {
    grid-template-columns: 1fr 1fr;
  }
}

.riskBlock {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  padding: 10px;
}

.riskBlock__hdr {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: var(--geo-font-size-title);
  font-weight: 600;
  margin-bottom: 6px;
}

.capRow {
  display: grid;
  grid-template-columns: 120px 1fr 56px;
  gap: 10px;
  align-items: center;
  margin-bottom: 10px;
}

.capRow__label {
  font-size: var(--geo-font-size-label);
  color: var(--el-text-color-secondary);
}

.capRow__value {
  font-size: 12px;
  font-weight: 600;
  text-align: right;
  color: var(--el-text-color-primary);
}

.hist {
  display: flex;
  align-items: flex-end;
  gap: 3px;
  height: 54px;
}

.hist__bar {
  width: 8px;
  background: var(--el-color-primary-light-5);
  border-radius: 3px;
}

.hist__labels {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  margin-top: 6px;
}

.muted {
  color: var(--el-text-color-secondary);
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
  font-size: 12px;
}

.cycles {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.cycleItem {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  padding: 10px;
}

.cycleTitle {
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 6px;
}

.cycleEdge {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.paneGrid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px 12px;
  align-items: start;
}

.paneGrid > .toggleGrid {
  grid-column: 1 / -1;
}

.ctl {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.toolbarLabel {
  font-size: var(--geo-font-size-label);
  font-weight: var(--geo-font-weight-label);
  color: var(--el-text-color-secondary);
}

.ctl__label {
  min-height: 18px;
}

.ctl__field {
  width: 100%;
}

.sliderField {
  width: 100%;
  min-width: 220px;
}

.ctl--eq {
  min-width: 160px;
}

.ctl--status {
  min-width: 220px;
}

.ctl--threshold {
  min-width: 160px;
}

.ctl--degree {
  min-width: 160px;
}

.ctl--layout {
  min-width: 200px;
}

.toggleGrid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 6px 12px;
  align-items: center;
}

.toggleLine {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 10px;
  padding: 2px 6px;
  min-height: 28px;
  border-radius: 8px;
}

.toggleLine:hover {
  background: var(--el-fill-color-light);
}

.toggleLine :deep(.el-switch) {
  flex: 0 0 auto;
}

.navPane {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.navRow {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 10px;
  align-items: center;
}

.navRow__label {
  min-width: 76px;
}

.navRow__field :deep(.el-autocomplete),
.navRow__field :deep(.el-input),
.navRow__field :deep(.el-input__wrapper) {
  width: 100%;
}

.navActions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.zoomrow {
  display: flex;
  align-items: center;
  gap: 8px;
}

.zoomrow__label {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.zoomrow__slider {
  width: clamp(160px, 18vw, 260px);
}

@media (max-width: 992px) {
  .paneGrid {
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  }

  .sliderField {
    min-width: 200px;
  }
}

@media (max-width: 768px) {
  .navRow {
    grid-template-columns: 1fr;
    align-items: start;
  }

  .navRow__label {
    min-width: 0;
  }

  .zoomrow__slider {
    width: clamp(180px, 60vw, 320px);
  }
}

.cy-wrap {
  position: relative;
  height: calc(100vh - 260px);
  min-height: 520px;
}

.legend {
  position: absolute;
  top: 10px;
  left: 10px;
  z-index: 2;
  background: rgba(17, 19, 23, 0.85);
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 12px;
  color: var(--el-text-color-primary);
  min-width: 240px;
  backdrop-filter: blur(4px);
}

.legend__title {
  font-weight: 600;
  margin-bottom: 8px;
}

.legend__row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 4px 0;
}

.swatch {
  display: inline-block;
  width: 18px;
  height: 10px;
  border-radius: 2px;
  border: 1px solid rgba(255, 255, 255, 0.18);
}

.swatch--node-active {
  width: 12px;
  height: 12px;
  border-radius: 6px;
  background: #67c23a;
}

.swatch--node-frozen {
  width: 12px;
  height: 12px;
  border-radius: 6px;
  background: #e6a23c;
}

.swatch--node-business {
  width: 14px;
  height: 10px;
  border-radius: 4px;
  background: #1f2d3d;
  border: 2px solid #409eff;
}

.swatch--edge-active {
  background: #409eff;
}

.swatch--edge-frozen {
  background: #909399;
}

.swatch--edge-closed {
  background: #a3a6ad;
}

.swatch--edge-bottleneck {
  background: #f56c6c;
  height: 10px;
}

.swatch--edge-incident {
  background: repeating-linear-gradient(90deg, #606266 0 4px, transparent 4px 7px);
}

.cy {
  height: 100%;
  width: 100%;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  background: var(--el-bg-color-overlay);
}
</style>
