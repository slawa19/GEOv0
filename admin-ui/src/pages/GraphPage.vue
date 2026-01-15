<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import cytoscape, { type Core, type EdgeSingular, type NodeSingular } from 'cytoscape'
import fcose from 'cytoscape-fcose'

import { api } from '../api'
import { assertSuccess } from '../api/envelope'
import { useGraphData } from '../composables/useGraphData'
import { useGraphAnalytics } from '../composables/useGraphAnalytics'
import type { DrawerTab, SelectedInfo } from '../composables/useGraphVisualization'
import { cycleDebtEdgeToTrustlineDirection } from '../utils/cycleMapping'
import { formatDecimalFixed, isRatioBelowThreshold } from '../utils/decimal'
import { throttle } from '../utils/throttle'
import { THROTTLE_GRAPH_REBUILD_MS, THROTTLE_LAYOUT_SPACING_MS } from '../constants/timing'
import {
  DEFAULT_FOCUS_DEPTH,
  DEFAULT_LAYOUT_SPACING,
  DEFAULT_THRESHOLD,
  MIN_ZOOM_LABELS_ALL,
  MIN_ZOOM_LABELS_PERSON,
  NODE_DOUBLE_TAP_MS,
  PARTICIPANT_SUGGESTIONS_LIMIT,
  SELECTED_PULSE_INTERVAL_MS,
} from '../constants/graph'
import TooltipLabel from '../ui/TooltipLabel.vue'
import GraphAnalyticsDrawer from './graph/GraphAnalyticsDrawer.vue'
import GraphLegend from './graph/GraphLegend.vue'
import GraphFiltersToolbar from './graph/GraphFiltersToolbar.vue'
import type {
  ClearingCycles,
  Participant,
  Trustline,
} from './graph/graphTypes'

cytoscape.use(fcose)

const cyRoot = ref<HTMLElement | null>(null)
let cy: Core | null = null
let zoomUpdatingFromCy = false

let lastNodeTapAt = 0
let lastNodeTapPid = ''

let pendingNodeTapTimer: number | null = null

function stopPendingNodeTap() {
  if (pendingNodeTapTimer !== null) {
    window.clearTimeout(pendingNodeTapTimer)
    pendingNodeTapTimer = null
  }
}

let selectedPulseTimer: number | null = null
let selectedPulseOn = false

function stopSelectedPulse() {
  if (selectedPulseTimer !== null) {
    window.clearInterval(selectedPulseTimer)
    selectedPulseTimer = null
  }
  selectedPulseOn = false
}

function applySelectedHighlight(pid: string) {
  if (!cy) return
  const p = String(pid || '').trim()

  cy.nodes('.selected-node').removeClass('selected-node')
  cy.nodes('.selected-pulse').removeClass('selected-pulse')

  stopSelectedPulse()

  if (!p) return
  const n = cy.getElementById(p)
  if (!n || n.empty()) return

  n.addClass('selected-node')

  // Blink by toggling a secondary class (Cytoscape has no CSS animations).
  selectedPulseTimer = window.setInterval(() => {
    if (!cy) return
    const nn = cy.getElementById(p)
    if (!nn || nn.empty()) return
    selectedPulseOn = !selectedPulseOn
    if (selectedPulseOn) nn.addClass('selected-pulse')
    else nn.removeClass('selected-pulse')
  }, SELECTED_PULSE_INTERVAL_MS)
}

const drawerTab = ref<DrawerTab>('summary')
const drawerEq = ref<string>('ALL')

const analyticsEq = computed(() => {
  const key = String(drawerEq.value || '').trim().toUpperCase()
  return key === 'ALL' ? null : key
})

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

type AnalyticsToggleKey = keyof AnalyticsToggles

type AnalyticsToggleItem = {
  key: AnalyticsToggleKey
  label: string
  tooltipText: string
  requires?: AnalyticsToggleKey
}

const summaryToggleItems: AnalyticsToggleItem[] = [
  {
    key: 'showRank',
    label: 'Rank / percentile',
    tooltipText: 'Your position among all participants by net balance for the selected equivalent.',
  },
  {
    key: 'showConcentration',
    label: 'Concentration risk',
    tooltipText: 'How concentrated your debts/credits are across counterparties (top1/top5 shares + HHI).',
  },
  {
    key: 'showCapacity',
    label: 'Trustline capacity',
    tooltipText: 'Aggregate trustline capacity around the participant: used% = total_used / total_limit.',
  },
  {
    key: 'showActivity',
    label: 'Activity / churn',
    tooltipText: 'Recent changes around the participant in rolling windows (7/30/90 days).',
  },
]

const balanceToggleItems: AnalyticsToggleItem[] = [
  {
    key: 'showRank',
    label: 'Rank / percentile',
    tooltipText: 'Rank/percentile of net balance across all participants (for selected equivalent).',
  },
  {
    key: 'showDistribution',
    label: 'Distribution histogram',
    tooltipText: 'Tiny histogram of net balance distribution across all participants (selected equivalent).',
  },
]

const riskToggleItems: AnalyticsToggleItem[] = [
  {
    key: 'showConcentration',
    label: 'Concentration risk',
    tooltipText: 'Top1/top5 shares and HHI derived from counterparty debt shares.',
  },
  {
    key: 'showCapacity',
    label: 'Trustline capacity',
    tooltipText: 'Aggregate incoming/outgoing used vs limit and bottleneck detection.',
  },
  {
    key: 'showBottlenecks',
    label: 'Bottlenecks',
    tooltipText: 'Show bottleneck count/list inside Trustline capacity (threshold-based).',
    requires: 'showCapacity',
  },
  {
    key: 'showActivity',
    label: 'Activity / churn',
    tooltipText: '7/30/90-day counts from fixtures timestamps (trustlines/incidents/audit/transactions).',
  },
]

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
const threshold = ref<string>(DEFAULT_THRESHOLD)

const typeFilter = ref<string[]>(['person', 'business'])
const minDegree = ref<number>(0)

type LabelMode = 'off' | 'name' | 'pid' | 'both'

const showLabels = ref(true)
const labelModeBusiness = ref<LabelMode>('name')
const labelModePerson = ref<LabelMode>('name')
const autoLabelsByZoom = ref(true)
const minZoomLabelsAll = ref(MIN_ZOOM_LABELS_ALL)
const minZoomLabelsPerson = ref(MIN_ZOOM_LABELS_PERSON)

const showIncidents = ref(true)
const hideIsolates = ref(true)
const showLegend = ref(false)

const layoutName = ref<'fcose' | 'grid' | 'circle'>('fcose')
const layoutSpacing = ref<number>(DEFAULT_LAYOUT_SPACING)

const toolbarTab = ref<'filters' | 'display' | 'navigate'>('filters')

const isRealMode = computed(() => (import.meta.env.VITE_API_MODE || 'mock').toString().toLowerCase() === 'real')
const selected = ref<SelectedInfo | null>(null)

watch(selected, () => {
  stopSelectedPulse()
})

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

const focusMode = ref(false)
const focusDepth = ref<1 | 2>(DEFAULT_FOCUS_DEPTH)
const focusRootPid = ref('')

const {
  loading,
  error,
  participants,
  trustlines,
  incidents,
  debts,
  clearingCycles,
  auditLog,
  transactions,
  availableEquivalents,
  precisionByEq,
  participantByPid,
  loadData,
  refreshForFocusMode,
} = useGraphData({
  eq,
  isRealMode,
  focusMode,
  focusRootPid,
  focusDepth,
  statusFilter,
})

const graphAnalytics = useGraphAnalytics({
  isRealMode,
  threshold,
  analyticsEq,

  precisionByEq,
  availableEquivalents,
  participantByPid,

  participants,
  trustlines,
  debts,
  incidents,
  auditLog,
  transactions,
  clearingCycles,

  selected,
})

const activeCycleKey = ref('')

const activeConnectionKey = ref('')

const drawerOpen = ref(false)

function suggestPidForFocus(): string {
  if (selected.value && selected.value.kind === 'node') return selected.value.pid
  const p = String(focusPid.value || '').trim()
  if (p) return p
  const fromQuery = extractPidFromText(searchQuery.value)
  return fromQuery || ''
}

function setFocusRoot(pid: string) {
  focusRootPid.value = String(pid || '').trim()
}

function ensureFocusRootPid() {
  if (String(focusRootPid.value || '').trim()) return
  setFocusRoot(suggestPidForFocus())
}

function useSelectedForFocus() {
  if (!selected.value || selected.value.kind !== 'node') return
  setFocusRoot(selected.value.pid)
  focusMode.value = true
  ensureFocusRootPid()
}

function clearFocusMode() {
  focusMode.value = false
  setFocusRoot('')
}

function clearCycleHighlight() {
  activeCycleKey.value = ''
  if (!cy) return
  cy.edges('.cycle-highlight').removeClass('cycle-highlight')
  cy.nodes('.cycle-node').removeClass('cycle-node')
}

function clearConnectionHighlight() {
  activeConnectionKey.value = ''
  if (!cy) return
  cy.edges('.connection-highlight').removeClass('connection-highlight')
  cy.nodes('.connection-node').removeClass('connection-node')
}

function highlightConnection(fromPid: string, toPid: string, eqCode: string) {
  if (!cy) return
  const from = String(fromPid || '').trim()
  const to = String(toPid || '').trim()
  const eq = normEq(eqCode)
  if (!from || !to || !eq) return

  cy.edges().forEach((edge) => {
    const src = String(edge.data('source') || '')
    const dst = String(edge.data('target') || '')
    const eeq = normEq(String(edge.data('equivalent') || ''))
    if (src === from && dst === to && eeq === eq) edge.addClass('connection-highlight')
  })

  const a = cy.getElementById(from)
  const b = cy.getElementById(to)
  if (a && !a.empty()) a.addClass('connection-node')
  if (b && !b.empty()) b.addClass('connection-node')
}

function cycleKey(cycle: Array<{ debtor: string; creditor: string; equivalent: string; amount: string }>): string {
  return (cycle || [])
    .map((e) => `${normEq(e.equivalent)}:${String(e.debtor || '')}->${String(e.creditor || '')}`)
    .join('|')
}

function highlightCycle(cycle: Array<{ debtor: string; creditor: string; equivalent: string; amount: string }>) {
  if (!cy) return

  const touchedPids = new Set<string>()
  for (const e of cycle || []) {
    const debtor = String(e.debtor || '').trim()
    const creditor = String(e.creditor || '').trim()
    const mapped = cycleDebtEdgeToTrustlineDirection({ debtor, creditor, equivalent: e.equivalent })
    if (!mapped) continue
    const { from, to, equivalent } = mapped

    // Note: cycle edges are debt edges (debtor -> creditor).
    // TrustLine direction in the graph is creditor -> debtor.
    cy.edges().forEach((edge) => {
      const src = String(edge.data('source') || '')
      const dst = String(edge.data('target') || '')
      const eeq = normEq(String(edge.data('equivalent') || ''))
      if (src === from && dst === to && eeq === equivalent) edge.addClass('cycle-highlight')
    })

    touchedPids.add(debtor)
    touchedPids.add(creditor)
  }

  for (const pid of touchedPids) {
    const n = cy.getElementById(pid)
    if (n && !n.empty()) n.addClass('cycle-node')
  }
}

function toggleCycleHighlight(cycle: Array<{ debtor: string; creditor: string; equivalent: string; amount: string }>) {
  if (!cy) return
  const key = cycleKey(cycle)
  if (!key) return

  if (activeCycleKey.value === key) {
    clearCycleHighlight()
    return
  }

  clearCycleHighlight()
  activeCycleKey.value = key
  highlightCycle(cycle)
}

function isCycleActive(cycle: Array<{ debtor: string; creditor: string; equivalent: string; amount: string }>): boolean {
  const key = cycleKey(cycle)
  return Boolean(key) && activeCycleKey.value === key
}

function money(v: string): string {
  return formatDecimalFixed(v, 2)
}

function pct(x: number, digits = 0): string {
  if (!Number.isFinite(x)) return '0%'
  const p = clamp(x * 100, 0, 100)
  return `${p.toFixed(digits)}%`
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

function normEq(v: string): string {
  return String(v || '').trim().toUpperCase()
}

function isBottleneck(t: Trustline): boolean {
  return isRatioBelowThreshold({ numerator: t.available, denominator: t.limit, threshold: threshold.value })
}

type ConnectionRow = {
  direction: 'incoming' | 'outgoing'
  counterparty_pid: string
  counterparty_name: string
  equivalent: string
  status: string
  limit: string
  used: string
  available: string
  bottleneck: boolean
}

function connectionRowsFromCy(pid: string): { incoming: ConnectionRow[]; outgoing: ConnectionRow[] } {
  const incoming: ConnectionRow[] = []
  const outgoing: ConnectionRow[] = []
  if (!cy) return { incoming, outgoing }

  cy.edges().forEach((e) => {
    const from = String(e.data('source') || '')
    const to = String(e.data('target') || '')
    if (from !== pid && to !== pid) return

    const eqCode = normEq(String(e.data('equivalent') || ''))
    const status = String(e.data('status') || '')
    const limit = String(e.data('limit') || '')
    const used = String(e.data('used') || '')
    const available = String(e.data('available') || '')

    const isOut = from === pid
    const cp = isOut ? to : from
    const p = participantByPid.value.get(cp)
    const name = String(p?.display_name || '').trim()
    const row: ConnectionRow = {
      direction: isOut ? 'outgoing' : 'incoming',
      counterparty_pid: cp,
      counterparty_name: name,
      equivalent: eqCode,
      status,
      limit,
      used,
      available,
      bottleneck: status === 'active' && isBottleneck({ from, to, equivalent: eqCode, status, limit, used, available, created_at: '' }),
    }

    if (isOut) outgoing.push(row)
    else incoming.push(row)
  })

  const sortKey = (r: ConnectionRow) => `${r.equivalent}|${r.bottleneck ? '0' : '1'}|${r.counterparty_pid}`
  incoming.sort((a, b) => sortKey(a).localeCompare(sortKey(b)))
  outgoing.sort((a, b) => sortKey(a).localeCompare(sortKey(b)))
  return { incoming, outgoing }
}

const selectedConnectionsIncoming = computed<ConnectionRow[]>(() => {
  if (!selected.value || selected.value.kind !== 'node') return []
  return connectionRowsFromCy(selected.value.pid).incoming
})

const selectedConnectionsOutgoing = computed<ConnectionRow[]>(() => {
  if (!selected.value || selected.value.kind !== 'node') return []
  return connectionRowsFromCy(selected.value.pid).outgoing
})

const connectionsPageSize = ref<number>(25)
const connectionsIncomingPage = ref<number>(1)
const connectionsOutgoingPage = ref<number>(1)

function pageSlice<T>(items: T[], page: number, pageSize: number): T[] {
  const p = Math.max(1, Math.floor(Number(page) || 1))
  const s = Math.max(1, Math.floor(Number(pageSize) || 25))
  const start = (p - 1) * s
  return (items || []).slice(start, start + s)
}

const selectedConnectionsIncomingPaged = computed(() => pageSlice(selectedConnectionsIncoming.value, connectionsIncomingPage.value, connectionsPageSize.value))
const selectedConnectionsOutgoingPaged = computed(() => pageSlice(selectedConnectionsOutgoing.value, connectionsOutgoingPage.value, connectionsPageSize.value))

watch(
  () => (selected.value && selected.value.kind === 'node' ? selected.value.pid : ''),
  () => {
    connectionsIncomingPage.value = 1
    connectionsOutgoingPage.value = 1
  }
)

watch(
  () => selectedConnectionsIncoming.value.length,
  (n) => {
    const maxPage = Math.max(1, Math.ceil(n / Math.max(1, connectionsPageSize.value)))
    if (connectionsIncomingPage.value > maxPage) connectionsIncomingPage.value = 1
  }
)

watch(
  () => selectedConnectionsOutgoing.value.length,
  (n) => {
    const maxPage = Math.max(1, Math.ceil(n / Math.max(1, connectionsPageSize.value)))
    if (connectionsOutgoingPage.value > maxPage) connectionsOutgoingPage.value = 1
  }
)

function onConnectionRowClick(row: ConnectionRow) {
  if (selected.value && selected.value.kind === 'node') {
    const basePid = selected.value.pid
    const cp = String(row.counterparty_pid || '').trim()
    const eqCode = normEq(row.equivalent)

    const fromPid = row.direction === 'outgoing' ? basePid : cp
    const toPid = row.direction === 'outgoing' ? cp : basePid
    const key = `${eqCode}|${fromPid}->${toPid}`

    if (activeConnectionKey.value === key) {
      clearConnectionHighlight()
    } else {
      clearConnectionHighlight()
      activeConnectionKey.value = key
      highlightConnection(fromPid, toPid, eqCode)
    }
  }

  goToPid(row.counterparty_pid)
}

const selectedBalanceRows = graphAnalytics.selectedBalanceRows

// Note: we keep split counterparties (creditors vs debtors) as the primary UI.

const selectedCounterpartySplit = graphAnalytics.selectedCounterpartySplit

const selectedConcentration = graphAnalytics.selectedConcentration

const netDistribution = graphAnalytics.netDistribution

const selectedRank = graphAnalytics.selectedRank

const selectedCapacity = graphAnalytics.selectedCapacity

const selectedActivity = graphAnalytics.selectedActivity

const selectedCycles = graphAnalytics.selectedCycles

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
  const focusEnabled = Boolean(focusMode.value)
  const focusRoot = String(focusRootPid.value || '').trim()
  const focusD = focusDepth.value
  const minDeg = focusEnabled ? 0 : Math.max(0, Number(minDegree.value) || 0)
  const focusedPid = String(focusPid.value || '').trim()

  const pIndex = new Map<string, Participant>()
  for (const p of participants.value || []) {
    if (p?.pid) pIndex.set(p.pid, p)
  }

  const typeOf = (pid: string): string => String(pIndex.get(pid)?.type || '').toLowerCase()
  const isTypeAllowed = (pid: string): boolean => {
    if (!allowedTypes.size) return true
    const t = typeOf(pid)
    if (!t) return false
    // Keep types strict: person|business|hub
    return allowedTypes.has(t)
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
  let pidSet = new Set<string>()
  for (const t of visibleEdges) {
    pidSet.add(t.from)
    pidSet.add(t.to)
  }

  // Focus Mode (ego graph): keep a small neighborhood around a root PID.
  // Depth is computed on the currently visible (type+status+eq filtered) edges.
  if (focusEnabled && focusRoot) {
    const adj = new Map<string, Set<string>>()
    for (const t of visibleEdges) {
      if (!adj.has(t.from)) adj.set(t.from, new Set())
      if (!adj.has(t.to)) adj.set(t.to, new Set())
      adj.get(t.from)!.add(t.to)
      adj.get(t.to)!.add(t.from)
    }

    const focusPids = new Set<string>()
    const q: Array<{ pid: string; depth: number }> = [{ pid: focusRoot, depth: 0 }]
    focusPids.add(focusRoot)

    while (q.length) {
      const cur = q.shift()!
      if (cur.depth >= focusD) continue
      const nb = adj.get(cur.pid)
      if (!nb) continue
      for (const n of nb) {
        if (focusPids.has(n)) continue
        focusPids.add(n)
        q.push({ pid: n, depth: cur.depth + 1 })
      }
    }

    // Always keep the root node (even if it has no edges under current filters).
    if (pIndex.has(focusRoot) && isTypeAllowed(focusRoot)) focusPids.add(focusRoot)
    pidSet = focusPids
  } else {
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
  const pinnedPid = focusEnabled ? focusRoot : focusedPid
  for (const pid of prelim) {
    const deg = degreeByPid.get(pid) || 0
    if (minDeg > 0 && deg < minDeg && pid !== pinnedPid) continue
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

    // Selection pulse as overlay glow (doesn't conflict with border-color based highlights).
    // Note: overlay color is keyed by status to feel consistent with the legend.
    { selector: 'node.selected-node, node.selected-pulse', style: { 'overlay-color': '#409eff' } },
    { selector: 'node.selected-node.p-active, node.selected-pulse.p-active', style: { 'overlay-color': '#67c23a' } },
    {
      selector: 'node.selected-node.p-frozen, node.selected-pulse.p-frozen, node.selected-node.p-suspended, node.selected-pulse.p-suspended',
      style: { 'overlay-color': '#e6a23c' },
    },
    { selector: 'node.selected-node.p-banned, node.selected-pulse.p-banned', style: { 'overlay-color': '#f56c6c' } },
    { selector: 'node.selected-node.p-deleted, node.selected-pulse.p-deleted', style: { 'overlay-color': '#909399' } },

    { selector: 'node.type-person', style: { shape: 'ellipse', width: 16, height: 16 } },
    {
      selector: 'node.type-business',
      style: {
        shape: 'round-rectangle',
        width: 26,
        height: 22,
        'border-width': 0,
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

    {
      selector: 'edge.cycle-highlight',
      style: {
        'line-color': '#e6a23c',
        'target-arrow-color': '#e6a23c',
        width: 3.2,
        opacity: 1,
        'arrow-scale': 1.05,
      },
    },
    {
      selector: 'node.cycle-node',
      style: {
        'border-width': 4,
        'border-color': '#e6a23c',
      },
    },

    {
      selector: 'edge.connection-highlight',
      style: {
        'line-color': '#67c23a',
        'target-arrow-color': '#67c23a',
        width: 3.0,
        opacity: 1,
        'arrow-scale': 1.05,
      },
    },
    {
      selector: 'node.connection-node',
      style: {
        'underlay-color': '#67c23a',
        'underlay-opacity': 0.35,
        'underlay-padding': 6,
      },
    },

    // Selection pulse: overlay opacity/padding only.
    { selector: 'node.selected-node', style: { 'overlay-opacity': 0.12, 'overlay-padding': 6 } },
    { selector: 'node.selected-pulse', style: { 'overlay-opacity': 0.22, 'overlay-padding': 12 } },
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
  const edgeWCycle = clamp(3.0 * inv, 0.7, 3.6)
  const edgeWConnection = clamp(3.0 * inv, 0.7, 3.6)
  const arrowScale = clamp(0.8 * s, 0.32, 1.0)
  const arrowScaleBottleneck = clamp(0.95 * s, 0.4, 1.15)
  const arrowScaleCycle = clamp(1.05 * s, 0.45, 1.25)
  const arrowScaleConnection = clamp(1.05 * s, 0.45, 1.25)

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
    .selector('edge.cycle-highlight')
    .style({
      width: edgeWCycle,
      'arrow-scale': arrowScaleCycle,
    })
    .selector('edge.connection-highlight')
    .style({
      width: edgeWConnection,
      'arrow-scale': arrowScaleConnection,
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
  clearCycleHighlight()
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

    const isBusiness = t === 'business'
    let mode: LabelMode = isBusiness ? labelModeBusiness.value : labelModePerson.value

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
    cb(visibleParticipantSuggestions().slice(0, PARTICIPANT_SUGGESTIONS_LIMIT))
    return
  }

  const results = visibleParticipantSuggestions()
    .filter((s) => s.value.toLowerCase().includes(q) || s.pid.toLowerCase().includes(q))
    .slice(0, PARTICIPANT_SUGGESTIONS_LIMIT)

  cb(results)
}

function goToPid(pid: string) {
  const p = String(pid || '').trim()
  if (!p) return
  searchQuery.value = p
  focusPid.value = p
  focusSearch()
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

    const displayName = String(n.data('display_name') || '').trim()
    const degree = n.degree(false)
    const inDegree = n.indegree(false)
    const outDegree = n.outdegree(false)
    selected.value = {
      kind: 'node',
      pid,
      display_name: displayName || undefined,
      status: String(n.data('status') || '') || undefined,
      type: String(n.data('type') || '') || undefined,
      degree,
      inDegree,
      outDegree,
    }

    // UX: clicking a node should prefill search (PID and name), but not necessarily move the camera.
    // This also enables quick navigation by pressing Enter / clicking Find.
    searchQuery.value = displayName ? `${displayName} — ${pid}` : pid

    // Double-click opens the details drawer. Single click does not.
    const now = Date.now()
    const prevPid = String(lastNodeTapPid || '')
    const dt = now - (lastNodeTapAt || 0)
    lastNodeTapAt = now
    lastNodeTapPid = pid

    const isDouble = prevPid === pid && dt > 0 && dt <= NODE_DOUBLE_TAP_MS
    if (isDouble) {
      // Cancel any pending single-click action (prevents re-layout between clicks).
      stopPendingNodeTap()

      // Center/zoom like Find (also adds a short search-hit highlight).
      focusPid.value = pid
      focusSearch()

      drawerTab.value = 'summary'
      drawerOpen.value = true
      return
    }

    // Single-click action is delayed: if the user double-clicks, this never runs.
    stopPendingNodeTap()
    pendingNodeTapTimer = window.setTimeout(() => {
      pendingNodeTapTimer = null
      // UX: when Focus Mode is enabled, clicking nodes should switch the focus root (stay in focus).
      // Guard: only if this node is still the selected one.
      if (focusMode.value && selected.value && selected.value.kind === 'node' && selected.value.pid === pid) {
        setFocusRoot(pid)
      }
    }, NODE_DOUBLE_TAP_MS + 50)
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
  stopPendingNodeTap()
  stopSelectedPulse()
  if (cy) {
    cy.destroy()
    cy = null
  }
})

const throttledRebuild = throttle(() => {
  if (!cy) return
  rebuildGraph({ fit: false })
}, THROTTLE_GRAPH_REBUILD_MS)

const throttledLayoutSpacing = throttle(() => {
  if (!cy) return
  runLayout()
}, THROTTLE_LAYOUT_SPACING_MS)

watch([eq, statusFilter, threshold, showIncidents, hideIsolates], () => {
  throttledRebuild()
})

watch([typeFilter, minDegree], () => {
  throttledRebuild()
})

watch([focusMode, focusDepth, focusRootPid], () => {
  if (focusMode.value) ensureFocusRootPid()
  void (async () => {
    await refreshForFocusMode()
    if (!cy) return
    rebuildGraph({ fit: true })
  })()
})

watch(
  () => (selected.value && selected.value.kind === 'node' ? selected.value.pid : ''),
  (pid) => {
    clearCycleHighlight()
    clearConnectionHighlight()
    void (async () => {
      if (isRealMode.value && !focusMode.value) {
        const p = String(pid || '').trim()
        if (p) {
          try {
            const cc = await api.clearingCycles({ participant_pid: p })
            clearingCycles.value = (assertSuccess(cc) as ClearingCycles | null) ?? null
          } catch {
            // keep previous
          }
        }
      }
      if (!cy) return
      applySelectedHighlight(pid)
    })()
  }
)

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
  throttledLayoutSpacing()
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

const canUseSelectedForFocus = computed(() => Boolean(selected.value && selected.value.kind === 'node'))

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
        <TooltipLabel
          label="Network Graph"
          tooltip-key="nav.graph"
        />
        <div class="hdr__right">
          <el-tag type="info">
            {{ seedLabel }}
          </el-tag>
          <el-tag type="info">
            Nodes: {{ stats.nodes }}
          </el-tag>
          <el-tag type="info">
            Edges: {{ stats.edges }}
          </el-tag>
          <el-tag
            v-if="stats.bottlenecks"
            type="danger"
          >
            Bottlenecks: {{ stats.bottlenecks }}
          </el-tag>
        </div>
      </div>
    </template>

    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      class="mb"
    />

    <GraphFiltersToolbar
      v-model:toolbar-tab="toolbarTab"
      v-model:eq="eq"
      v-model:status-filter="statusFilter"
      v-model:threshold="threshold"
      v-model:type-filter="typeFilter"
      v-model:min-degree="minDegree"
      v-model:layout-name="layoutName"
      v-model:layout-spacing="layoutSpacing"
      v-model:business-label-parts="businessLabelParts"
      v-model:person-label-parts="personLabelParts"
      v-model:show-labels="showLabels"
      v-model:auto-labels-by-zoom="autoLabelsByZoom"
      v-model:show-incidents="showIncidents"
      v-model:hide-isolates="hideIsolates"
      v-model:show-legend="showLegend"
      v-model:search-query="searchQuery"
      v-model:focus-pid="focusPid"
      v-model:zoom="zoom"
      v-model:focus-mode="focusMode"
      v-model:focus-depth="focusDepth"
      :available-equivalents="availableEquivalents"
      :statuses="statuses"
      :layout-options="layoutOptions"
      :fetch-suggestions="querySearchParticipants"
      :on-focus-search="focusSearch"
      :on-fit="fit"
      :on-relayout="runLayout"
      :can-find="canFind"
      :focus-root-pid="focusRootPid"
      :can-use-selected-for-focus="canUseSelectedForFocus"
      :on-use-selected-for-focus="useSelectedForFocus"
      :on-clear-focus-mode="clearFocusMode"
    />

    <el-skeleton
      v-if="loading"
      animated
      :rows="6"
    />

    <div
      v-else
      class="cy-wrap"
    >
      <GraphLegend :open="showLegend" />
      <div
        ref="cyRoot"
        class="cy"
      />
    </div>
  </el-card>

  <GraphAnalyticsDrawer
    v-model="drawerOpen"
    v-model:tab="drawerTab"
    v-model:eq="drawerEq"
    v-model:analytics="analytics"
    v-model:connections-incoming-page="connectionsIncomingPage"
    v-model:connections-outgoing-page="connectionsOutgoingPage"
    :selected="selected"
    :show-incidents="showIncidents"
    :incident-ratio-by-pid="incidentRatioByPid"
    :available-equivalents="availableEquivalents"
    :analytics-eq="analyticsEq"
    :threshold="threshold"
    :precision-by-eq="precisionByEq"
    :atoms-to-decimal="atomsToDecimal"
    :load-data="loadData"
    :money="money"
    :pct="pct"
    :selected-rank="selectedRank"
    :selected-concentration="selectedConcentration"
    :selected-capacity="selectedCapacity"
    :selected-activity="selectedActivity"
    :net-distribution="netDistribution"
    :selected-balance-rows="selectedBalanceRows"
    :selected-counterparty-split="selectedCounterpartySplit"
    :selected-connections-incoming="selectedConnectionsIncoming"
    :selected-connections-outgoing="selectedConnectionsOutgoing"
    :selected-connections-incoming-paged="selectedConnectionsIncomingPaged"
    :selected-connections-outgoing-paged="selectedConnectionsOutgoingPaged"
    :connections-page-size="connectionsPageSize"
    :on-connection-row-click="onConnectionRowClick"
    :selected-cycles="selectedCycles"
    :is-cycle-active="isCycleActive"
    :toggle-cycle-highlight="toggleCycleHighlight"
    :summary-toggle-items="summaryToggleItems"
    :balance-toggle-items="balanceToggleItems"
    :risk-toggle-items="riskToggleItems"
  />
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

.pidLink {
  color: var(--el-color-primary);
}

.clickable-table :deep(tr) {
  cursor: pointer;
}

.tableTop {
  display: flex;
  justify-content: flex-end;
  margin: 6px 0 8px;
}

.navFocus--drawer {
  padding: 2px 0;
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
  cursor: pointer;
}

.cycleItem--active {
  border-color: var(--el-color-warning);
  box-shadow: 0 0 0 1px var(--el-color-warning) inset;
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


.cy-wrap {
  position: relative;
  height: calc(100vh - 260px);
  min-height: 520px;
}

.cy {
  height: 100%;
  width: 100%;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  background: var(--el-bg-color-overlay);
}
</style>
