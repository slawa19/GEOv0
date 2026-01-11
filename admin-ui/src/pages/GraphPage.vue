<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import cytoscape, { type Core, type EdgeSingular, type NodeSingular } from 'cytoscape'
import fcose from 'cytoscape-fcose'

import { loadFixtureJson } from '../api/fixtures'
import { formatDecimalFixed, isRatioBelowThreshold } from '../utils/decimal'

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

const showLabels = ref(false)
const showIncidents = ref(true)
const hideIsolates = ref(true)

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

  const nodes = Array.from(pidSet).map((pid) => {
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
        showIncidents.value && (incidentRatioByPid.value.get(pid) || 0) > 0 ? 'has-incident' : '',
      ]
        .filter(Boolean)
        .join(' '),
    }
  })

  const edges = tls.map((t, idx) => {
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
    { selector: 'node.p-suspended', style: { 'background-color': '#e6a23c' } },
    { selector: 'node.p-deleted', style: { 'background-color': '#909399' } },

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

watch(layoutName, () => {
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
  const tls = filteredTrustlines.value
  const n = new Set<string>()
  for (const t of tls) {
    n.add(t.from)
    n.add(t.to)
  }
  const bottlenecks = tls.filter((t) => t.status === 'active' && isBottleneck(t)).length
  return { nodes: n.size, edges: tls.length, bottlenecks }
})
</script>

<template>
  <el-card>
    <template #header>
      <div class="hdr">
        <div>Network Graph</div>
        <div class="hdr__right">
          <el-tag type="info">nodes: {{ stats.nodes }}</el-tag>
          <el-tag type="info">edges: {{ stats.edges }}</el-tag>
          <el-tag v-if="stats.bottlenecks" type="danger">bottlenecks: {{ stats.bottlenecks }}</el-tag>
        </div>
      </div>
    </template>

    <el-alert v-if="error" :title="error" type="error" show-icon class="mb" />

    <div class="controls">
      <el-select v-model="eq" size="small" style="width: 160px">
        <el-option v-for="c in availableEquivalents" :key="c" :label="c" :value="c" />
      </el-select>

      <el-select v-model="statusFilter" multiple collapse-tags collapse-tags-tooltip size="small" style="width: 220px">
        <el-option v-for="s in statuses" :key="s.value" :label="s.label" :value="s.value" />
      </el-select>

      <el-input v-model="threshold" size="small" style="width: 120px" placeholder="0.10" />

      <el-select v-model="layoutName" size="small" style="width: 150px">
        <el-option v-for="o in layoutOptions" :key="o.value" :label="o.label" :value="o.value" />
      </el-select>

      <el-switch v-model="showLabels" size="small" active-text="Labels" />
      <el-switch v-model="showIncidents" size="small" active-text="Incidents" />
      <el-switch v-model="hideIsolates" size="small" active-text="Hide isolates" />

      <div class="spacer" />

      <el-input v-model="searchPid" size="small" style="width: 260px" placeholder="Search PID" @keyup.enter="focusSearch" />
      <el-button size="small" @click="focusSearch">Find</el-button>
      <el-button size="small" @click="fit">Fit</el-button>
      <el-button size="small" @click="runLayout">Re-layout</el-button>
    </div>

    <el-skeleton v-if="loading" animated :rows="6" />

    <div v-else class="cy-wrap">
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
