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

const participants = ref<Participant[]>([])
const trustlines = ref<Trustline[]>([])
const incidents = ref<Incident[]>([])
const equivalents = ref<Equivalent[]>([])

const eq = ref<string>('ALL')
const statusFilter = ref<string[]>(['active', 'frozen', 'closed'])
const threshold = ref<string>('0.10')

const typeFilter = ref<string[]>(['person', 'business'])
const minDegree = ref<number>(0)

const showLabels = ref(false)
const showIncidents = ref(true)
const hideIsolates = ref(true)
const showLegend = ref(true)

const layoutName = ref<'fcose' | 'grid' | 'circle'>('fcose')

const searchPid = ref('')

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
  const focusPid = String(searchPid.value || '').trim()

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
    if (allowedTypes.size && t && !allowedTypes.has(t)) continue
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
    if (minDeg > 0 && deg < minDeg && pid !== focusPid) continue
    finalPids.add(pid)
  }

  const nodes = Array.from(finalPids).map((pid) => {
    const p = pIndex.get(pid)
    const ratio = incidentRatioByPid.value.get(pid)
    return {
      data: {
        id: pid,
        label: p?.display_name ? `${p.display_name} (${pid})` : pid,
        pid,
        display_name: p?.display_name || '',
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
        'font-size': 10,
        'text-wrap': 'wrap',
        'text-max-width': '180px',
        'text-background-opacity': 0,
        'border-width': 1,
        'border-color': '#2b2f36',
      },
    },
    { selector: 'node.p-active', style: { 'background-color': '#67c23a' } },
    { selector: 'node.p-frozen', style: { 'background-color': '#e6a23c' } },
    { selector: 'node.p-suspended', style: { 'background-color': '#e6a23c' } },
    { selector: 'node.p-banned', style: { 'background-color': '#f56c6c' } },
    { selector: 'node.p-deleted', style: { 'background-color': '#909399' } },

    { selector: 'node.type-business', style: { shape: 'round-rectangle', 'border-width': 2, 'border-color': '#409eff' } },

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
        width: 2,
        'curve-style': 'bezier',
        'line-color': '#606266',
        'target-arrow-shape': 'triangle',
        'target-arrow-color': '#606266',
        opacity: 0.85,
      },
    },
    { selector: 'edge.tl-active', style: { 'line-color': '#409eff', 'target-arrow-color': '#409eff' } },
    { selector: 'edge.tl-frozen', style: { 'line-color': '#909399', 'target-arrow-color': '#909399', opacity: 0.65 } },
    { selector: 'edge.tl-closed', style: { 'line-color': '#a3a6ad', 'target-arrow-color': '#a3a6ad', opacity: 0.45 } },

    { selector: 'edge.bottleneck', style: { 'line-color': '#f56c6c', 'target-arrow-color': '#f56c6c', width: 4, opacity: 1 } },

    {
      selector: 'edge.incident',
      style: {
        'line-style': 'dashed',
      },
    },
  ])
}

function runLayout() {
  if (!cy) return

  const name = layoutName.value
  const layout =
    name === 'grid'
      ? cy.layout({ name: 'grid', padding: 30 })
      : name === 'circle'
        ? cy.layout({ name: 'circle', padding: 30 })
        : cy.layout({
            name: 'fcose',
            animate: true,
            randomize: true,
            padding: 30,
            quality: 'default',
            nodeSeparation: 50,
          } as any)

  layout.run()
}

function rebuildGraph({ fit }: { fit: boolean }) {
  if (!cy) return
  const { nodes, edges } = buildElements()
  cy.elements().remove()
  cy.add(nodes)
  cy.add(edges)

  applyStyle()
  runLayout()
  if (fit) cy.fit(undefined, 30)
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
  cy.fit(undefined, 30)
}

function focusSearch() {
  if (!cy) return
  const pid = String(searchPid.value || '').trim()
  if (!pid) return

  const n = cy.getElementById(pid)
  if (!n || n.empty()) {
    ElMessage.warning(`PID not found: ${pid}`)
    return
  }

  cy.animate({ center: { eles: n }, zoom: Math.max(1.2, cy.zoom()) }, { duration: 300 })
  n.addClass('search-hit')
  setTimeout(() => n.removeClass('search-hit'), 900)
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

  attachHandlers()
  rebuildGraph({ fit: true })
})

onBeforeUnmount(() => {
  if (cy) {
    cy.destroy()
    cy = null
  }
})

watch([eq, statusFilter, threshold, showLabels, showIncidents, hideIsolates], () => {
  if (!cy) return
  rebuildGraph({ fit: false })
})

watch([typeFilter, minDegree], () => {
  if (!cy) return
  rebuildGraph({ fit: false })
})

watch(layoutName, () => {
  if (!cy) return
  runLayout()
})

const statuses = [
  { label: 'active', value: 'active' },
  { label: 'frozen', value: 'frozen' },
  { label: 'closed', value: 'closed' },
]

const typeOptions = [
  { label: 'person', value: 'person' },
  { label: 'business', value: 'business' },
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
  const pid = String(searchPid.value || '').trim()
  if (pid) return pid
  if (selected.value && selected.value.kind === 'node') return selected.value.pid
  return null
}

function fitNeighborhood() {
  if (!cy) return
  const pid = getZoomPid()
  if (!pid) {
    ElMessage.info('Set Search PID (or click a node)')
    return
  }

  const n = cy.getElementById(pid)
  if (!n || n.empty()) {
    ElMessage.warning(`PID not found: ${pid}`)
    return
  }

  const eles = (n as any).closedNeighborhood ? (n as any).closedNeighborhood() : n.neighborhood().union(n)
  cy.animate({ fit: { eles, padding: 60 } }, { duration: 300 })
}

function fitComponent() {
  if (!cy) return
  const pid = getZoomPid()
  if (!pid) {
    ElMessage.info('Set Search PID (or click a node)')
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

    <div class="controls">
      <TooltipLabel label="eq" tooltip-key="graph.eq" />
      <el-select v-model="eq" size="small" style="width: 160px">
        <el-option v-for="c in availableEquivalents" :key="c" :label="c" :value="c" />
      </el-select>

      <TooltipLabel label="status" tooltip-key="graph.status" />
      <el-select v-model="statusFilter" multiple collapse-tags collapse-tags-tooltip size="small" style="width: 220px">
        <el-option v-for="s in statuses" :key="s.value" :label="s.label" :value="s.value" />
      </el-select>

      <TooltipLabel label="threshold" tooltip-key="graph.threshold" />
      <el-input v-model="threshold" size="small" style="width: 120px" placeholder="0.10" />

      <TooltipLabel label="layout" tooltip-key="graph.layout" />
      <el-select v-model="layoutName" size="small" style="width: 150px">
        <el-option v-for="o in layoutOptions" :key="o.value" :label="o.label" :value="o.value" />
      </el-select>

      <TooltipLabel label="type" tooltip-key="graph.type" />
      <el-select v-model="typeFilter" multiple collapse-tags collapse-tags-tooltip size="small" style="width: 210px">
        <el-option v-for="t in typeOptions" :key="t.value" :label="t.label" :value="t.value" />
      </el-select>

      <TooltipLabel label="min degree" tooltip-key="graph.minDegree" />
      <el-input-number v-model="minDegree" size="small" :min="0" :max="20" controls-position="right" style="width: 140px" />

      <TooltipLabel label="Labels" tooltip-key="graph.labels" />
      <el-switch v-model="showLabels" size="small" />
      <TooltipLabel label="Incidents" tooltip-key="graph.incidents" />
      <el-switch v-model="showIncidents" size="small" />
      <TooltipLabel label="Hide isolates" tooltip-key="graph.hideIsolates" />
      <el-switch v-model="hideIsolates" size="small" />
      <TooltipLabel label="Legend" tooltip-key="graph.legend" />
      <el-switch v-model="showLegend" size="small" />

      <div class="spacer" />

      <TooltipLabel label="Search PID" tooltip-key="graph.search" />
      <el-input v-model="searchPid" size="small" style="width: 260px" placeholder="PID_U0006_b54cda26" @keyup.enter="focusSearch" />
      <el-button size="small" @click="focusSearch">Find</el-button>
      <TooltipLabel label="Zoom" tooltip-key="graph.zoom" />
      <el-button size="small" @click="fit">Fit</el-button>
      <el-button size="small" @click="fitNeighborhood">Fit neighborhood</el-button>
      <el-button size="small" @click="fitComponent">Fit component</el-button>
      <el-button size="small" @click="runLayout">Re-layout</el-button>
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

.controls {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.spacer {
  flex: 1;
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
