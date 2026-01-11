<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import cytoscape, { type Core, type EdgeSingular, type NodeSingular } from 'cytoscape'
import fcose from 'cytoscape-fcose'

import { loadFixtureJson } from '../api/fixtures'
import { formatDecimalFixed, isRatioBelowThreshold } from '../utils/decimal'
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

type Incident = {
  tx_id: string
  state: string
  initiator_pid: string
  equivalent: string
  age_seconds: number
  sla_seconds: number
  created_at?: string
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
const showLegend = ref(true)

const layoutName = ref<'fcose' | 'grid' | 'circle'>('fcose')
const layoutSpacing = ref<number>(1.6)

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
  const tls = filteredTrustlines.value

  const allowedTypes = new Set((typeFilter.value || []).map((t) => String(t).toLowerCase()).filter(Boolean))
  const minDeg = Math.max(0, Number(minDegree.value) || 0)
  const focusedPid = String(focusPid.value || '').trim()

  const pidSet = new Set<string>()
  for (const t of tls) {
    pidSet.add(t.from)
    pidSet.add(t.to)
  }

  if (!hideIsolates.value) {
    for (const p of participants.value || []) {
      if (p?.pid) pidSet.add(p.pid)
    }
  }

  const pIndex = new Map<string, Participant>()
  for (const p of participants.value || []) {
    if (p?.pid) pIndex.set(p.pid, p)
  }

  const prelim = new Set<string>()
  for (const pid of pidSet) {
    const p = pIndex.get(pid)
    const t = String(p?.type || '').toLowerCase()
    if (allowedTypes.size && !allowedTypes.has(t)) continue
    prelim.add(pid)
  }

  const filteredEdges = tls.filter((t) => prelim.has(t.from) && prelim.has(t.to))

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
        width: 2,
        'curve-style': 'bezier',
        'line-color': '#606266',
        'target-arrow-shape': 'triangle',
        'target-arrow-color': '#606266',
        'arrow-scale': 0.9,
        opacity: 0.85,
      },
    },
    { selector: 'edge.tl-active', style: { 'line-color': '#409eff', 'target-arrow-color': '#409eff' } },
    { selector: 'edge.tl-frozen', style: { 'line-color': '#909399', 'target-arrow-color': '#909399', opacity: 0.65 } },
    { selector: 'edge.tl-closed', style: { 'line-color': '#a3a6ad', 'target-arrow-color': '#a3a6ad', opacity: 0.45 } },

    { selector: 'edge.bottleneck', style: { 'line-color': '#f56c6c', 'target-arrow-color': '#f56c6c', width: 4, 'arrow-scale': 1.05, opacity: 1 } },

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

  const edgeW = clamp(1.8 * inv, 0.35, 2.2)
  const edgeWBottleneck = clamp(3.6 * inv, 0.8, 4)
  const arrowScale = clamp(0.9 * s, 0.35, 1.1)
  const arrowScaleBottleneck = clamp(1.05 * s, 0.45, 1.25)

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
            animate: true,
            randomize: true,
            padding: 40,
            quality: spacing >= 1.4 ? 'proof' : 'default',
            nodeSeparation: Math.round(70 * spacing),
            idealEdgeLength: Math.round(80 * spacing),
            nodeRepulsion: Math.round(4500 * spacing * spacing),
            gravity: 0.25,
            avoidOverlap: true,
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
  cy.nodes().forEach((n) => {
    const pid = String(n.data('pid') || n.id())
    const displayName = String(n.data('display_name') || '')
    const t = String(n.data('type') || '').toLowerCase()

    let mode: LabelMode = t === 'business' ? labelModeBusiness.value : labelModePerson.value

    if (autoLabelsByZoom.value) {
      if (z < minZoomLabelsAll.value) {
        mode = 'off'
      } else if (t === 'person' && z < minZoomLabelsPerson.value) {
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
    const [ps, tls, inc, eqs] = await Promise.all([
      loadFixtureJson<Participant[]>('datasets/participants.json'),
      loadFixtureJson<Trustline[]>('datasets/trustlines.json'),
      loadFixtureJson<{ items: Incident[] }>('datasets/incidents.json'),
      loadFixtureJson<Equivalent[]>('datasets/equivalents.json'),
    ])

    participants.value = ps
    trustlines.value = tls
    incidents.value = inc.items
    equivalents.value = eqs

    if (!availableEquivalents.value.includes(eq.value)) eq.value = 'ALL'
  } catch (e: any) {
    error.value = e?.message || 'Failed to load fixtures'
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadData()

  if (!cyRoot.value) return
  cy = cytoscape({
    container: cyRoot.value,
    elements: [],
    minZoom: 0.1,
    maxZoom: 3,
    wheelSensitivity: 0.15,
  })

  cy.on('zoom', () => {
    if (!cy) return
    zoomUpdatingFromCy = true
    zoom.value = cy.zoom()
    zoomUpdatingFromCy = false

    // Keep styling/labels responsive to mouse wheel / pinch zoom.
    updateZoomStyles()
    updateLabelsForZoom()
  })

  attachHandlers()
  rebuildGraph({ fit: true })

  zoomUpdatingFromCy = true
  zoom.value = cy.zoom()
  zoomUpdatingFromCy = false
})

onBeforeUnmount(() => {
  if (cy) {
    cy.destroy()
    cy = null
  }
})

watch([eq, statusFilter, threshold, showIncidents, hideIsolates], () => {
  if (!cy) return
  rebuildGraph({ fit: false })
})

watch([typeFilter, minDegree], () => {
  if (!cy) return
  rebuildGraph({ fit: false })
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

const canFitComponent = computed(() => Boolean(getZoomPid()))

function fitComponent() {
  if (!cy) return
  const pid = getZoomPid()
  if (!pid) {
    ElMessage.info('Select a participant (or click a node)')
    return
  }

  const start = cy.getElementById(pid)
  if (!start || start.empty()) {
    ElMessage.warning(`PID not found: ${pid}`)
    return
  }

  const visited = new Set<string>()
  const q: NodeSingular[] = [start as unknown as NodeSingular]
  let comp = cy.collection()

  while (q.length) {
    const cur = q.pop()!
    const id = cur.id()
    if (!id || visited.has(id)) continue
    visited.add(id)
    comp = comp.union(cur).union(cur.connectedEdges())
    cur
      .connectedEdges()
      .connectedNodes()
      .forEach((n: any) => {
        const nn = n as unknown as NodeSingular
        if (!visited.has(nn.id())) q.push(nn)
      })
  }

  cy.animate({ fit: { eles: comp, padding: 60 } }, { duration: 300 })

  zoomUpdatingFromCy = true
  zoom.value = cy.zoom()
  zoomUpdatingFromCy = false

  updateZoomStyles()
  updateLabelsForZoom()
}

function applyZoom(level: number) {
  if (!cy) return
  const z = Math.min(cy.maxZoom(), Math.max(cy.minZoom(), level))
  const center = { x: cy.width() / 2, y: cy.height() / 2 }
  cy.zoom({ level: z, renderedPosition: center })
}
</script>

<template>
  <el-card>
    <template #header>
      <div class="hdr">
        <TooltipLabel label="Network Graph" tooltip-key="nav.graph" />
        <div class="hdr__right">
          <el-tag type="info">nodes: {{ stats.nodes }}</el-tag>
          <el-tag type="info">edges: {{ stats.edges }}</el-tag>
          <el-tag v-if="stats.bottlenecks" type="danger">bottlenecks: {{ stats.bottlenecks }}</el-tag>
        </div>
      </div>
    </template>

    <el-alert v-if="error" :title="error" type="error" show-icon class="mb" />

    <div class="toolbar">
      <div class="toolbar__group">
        <div class="toolbar__title">Filters</div>
        <div class="toolbar__items">
          <div class="ctl">
            <TooltipLabel class="ctl__label" label="Equivalent" tooltip-key="graph.eq" />
            <el-select v-model="eq" size="small" style="width: 180px">
              <el-option v-for="c in availableEquivalents" :key="c" :label="c" :value="c" />
            </el-select>
          </div>

          <div class="ctl">
            <TooltipLabel class="ctl__label" label="Status" tooltip-key="graph.status" />
            <el-select v-model="statusFilter" multiple collapse-tags collapse-tags-tooltip size="small" style="width: 240px">
              <el-option v-for="s in statuses" :key="s.value" :label="s.label" :value="s.value" />
            </el-select>
          </div>

          <div class="ctl">
            <TooltipLabel class="ctl__label" label="Bottleneck" tooltip-key="graph.threshold" />
            <el-input v-model="threshold" size="small" style="width: 140px" placeholder="0.10" />
          </div>

          <div class="ctl">
            <TooltipLabel class="ctl__label" label="Type" tooltip-key="graph.type" />
            <el-checkbox-group v-model="typeFilter" size="small">
              <el-checkbox-button label="person">person</el-checkbox-button>
              <el-checkbox-button label="business">business</el-checkbox-button>
            </el-checkbox-group>
          </div>

          <div class="ctl">
            <TooltipLabel class="ctl__label" label="Min degree" tooltip-key="graph.minDegree" />
            <el-input-number v-model="minDegree" size="small" :min="0" :max="20" controls-position="right" style="width: 160px" />
          </div>
        </div>
      </div>

      <div class="toolbar__group">
        <div class="toolbar__title">Display</div>
        <div class="toolbar__items toolbar__items--grid">
          <div class="ctl">
            <TooltipLabel class="ctl__label" label="Layout" tooltip-key="graph.layout" />
            <el-select v-model="layoutName" size="small" style="width: 180px">
              <el-option v-for="o in layoutOptions" :key="o.value" :label="o.label" :value="o.value" />
            </el-select>
          </div>

          <div class="ctl ctl--toggle">
            <TooltipLabel class="ctl__label" label="Labels" tooltip-key="graph.labels" />
            <el-switch v-model="showLabels" size="small" />
          </div>

          <div class="ctl">
            <TooltipLabel class="ctl__label" label="Business labels" tooltip-key="graph.labels" />
            <el-checkbox-group v-model="businessLabelParts" size="small">
              <el-checkbox-button label="name">name</el-checkbox-button>
              <el-checkbox-button label="pid">pid</el-checkbox-button>
            </el-checkbox-group>
          </div>

          <div class="ctl">
            <TooltipLabel class="ctl__label" label="Person labels" tooltip-key="graph.labels" />
            <el-checkbox-group v-model="personLabelParts" size="small">
              <el-checkbox-button label="name">name</el-checkbox-button>
              <el-checkbox-button label="pid">pid</el-checkbox-button>
            </el-checkbox-group>
          </div>

          <div class="ctl ctl--toggle">
            <TooltipLabel class="ctl__label" label="Auto labels" tooltip-key="graph.labels" />
            <el-switch v-model="autoLabelsByZoom" size="small" />
          </div>

          <div class="ctl">
            <TooltipLabel class="ctl__label" label="Layout spacing" tooltip-key="graph.spacing" />
            <el-slider v-model="layoutSpacing" :min="1" :max="3" :step="0.1" style="width: 220px" />
          </div>

          <div class="ctl ctl--toggle">
            <TooltipLabel class="ctl__label" label="Incidents" tooltip-key="graph.incidents" />
            <el-switch v-model="showIncidents" size="small" />
          </div>

          <div class="ctl ctl--toggle">
            <TooltipLabel class="ctl__label" label="Hide isolates" tooltip-key="graph.hideIsolates" />
            <el-switch v-model="hideIsolates" size="small" />
          </div>

          <div class="ctl ctl--toggle">
            <TooltipLabel class="ctl__label" label="Legend" tooltip-key="graph.legend" />
            <el-switch v-model="showLegend" size="small" />
          </div>
        </div>
      </div>

      <div class="toolbar__group toolbar__group--wide">
        <div class="toolbar__title">Navigate</div>
        <div class="toolbar__items">
          <div class="ctl ctl--wide">
            <TooltipLabel class="ctl__label" label="Search (PID or name)" tooltip-key="graph.search" />
            <el-autocomplete
              v-model="searchQuery"
              :fetch-suggestions="querySearchParticipants"
              placeholder="Type PID or name…"
              size="small"
              clearable
              @select="onSearchSelect"
              @keyup.enter="focusSearch"
            />
          </div>

          <div class="ctl ctl--buttons">
            <TooltipLabel class="ctl__label" label="Actions" tooltip-key="graph.actions" />
            <div class="btnrow">
              <el-button size="small" :disabled="!canFind" @click="focusSearch">Find</el-button>
              <el-button size="small" @click="fit">Fit</el-button>
              <el-button size="small" :disabled="!canFitComponent" @click="fitComponent">Fit component</el-button>
              <el-button size="small" @click="runLayout">Re-layout</el-button>
            </div>
          </div>

          <div class="ctl">
            <TooltipLabel class="ctl__label" label="Zoom" tooltip-key="graph.zoom" />
            <el-slider v-model="zoom" :min="0.1" :max="3" :step="0.05" style="width: 240px" />
          </div>
        </div>
      </div>
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
        <el-descriptions-item label="display_name">{{ selected.display_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="status">{{ selected.status || '-' }}</el-descriptions-item>
        <el-descriptions-item label="type">{{ selected.type || '-' }}</el-descriptions-item>
        <el-descriptions-item label="degree">{{ selected.degree }}</el-descriptions-item>
        <el-descriptions-item label="in/out">{{ selected.inDegree }} / {{ selected.outDegree }}</el-descriptions-item>
        <el-descriptions-item v-if="showIncidents" label="incident_ratio">
          {{ (incidentRatioByPid.get(selected.pid) || 0).toFixed(2) }}
        </el-descriptions-item>
      </el-descriptions>
    </div>

    <div v-else-if="selected && selected.kind === 'edge'">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="equivalent">{{ selected.equivalent }}</el-descriptions-item>
        <el-descriptions-item label="from">{{ selected.from }}</el-descriptions-item>
        <el-descriptions-item label="to">{{ selected.to }}</el-descriptions-item>
        <el-descriptions-item label="status">{{ selected.status }}</el-descriptions-item>
        <el-descriptions-item label="limit">{{ money(selected.limit) }}</el-descriptions-item>
        <el-descriptions-item label="used">{{ money(selected.used) }}</el-descriptions-item>
        <el-descriptions-item label="available">{{ money(selected.available) }}</el-descriptions-item>
        <el-descriptions-item label="created_at">{{ selected.created_at }}</el-descriptions-item>
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
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.toolbar__group {
  flex: 1 1 360px;
  border: 1px solid var(--el-border-color);
  border-radius: 10px;
  padding: 10px 12px;
  background: var(--el-fill-color-extra-light);
}

.toolbar__group--wide {
  flex: 2 1 520px;
}

.toolbar__title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  margin-bottom: 8px;
  letter-spacing: 0.2px;
}

.toolbar__items {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-end;
}

.toolbar__items--grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  align-items: end;
}

.ctl {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.ctl__label {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.ctl--toggle {
  min-width: 110px;
}

.ctl--wide {
  min-width: 300px;
  flex: 1 1 340px;
}

.ctl--buttons {
  align-self: flex-end;
}

.btnrow {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
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
