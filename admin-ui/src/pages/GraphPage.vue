<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Core } from 'cytoscape'
import { useRoute, useRouter } from 'vue-router'
import { useGraphData } from '../composables/useGraphData'
import { useGraphAnalytics } from '../composables/useGraphAnalytics'
import { useGraphVisualization } from '../composables/useGraphVisualization'
import type { DrawerTab, LabelMode, SelectedInfo } from '../composables/useGraphVisualization'
import {
  DEFAULT_FOCUS_DEPTH,
  DEFAULT_LAYOUT_SPACING,
  DEFAULT_THRESHOLD,
  MIN_ZOOM_LABELS_ALL,
  MIN_ZOOM_LABELS_PERSON,
} from '../constants/graph'
import TooltipLabel from '../ui/TooltipLabel.vue'
import LoadErrorAlert from '../ui/LoadErrorAlert.vue'
import { t } from '../i18n'
import GraphAnalyticsDrawer from './graph/GraphAnalyticsDrawer.vue'
import GraphLegend from './graph/GraphLegend.vue'
import GraphFiltersToolbar from './graph/GraphFiltersToolbar.vue'
import {
  DEFAULT_ANALYTICS_TOGGLES,
  type AnalyticsToggles,
  balanceToggleItems,
  riskToggleItems,
  summaryToggleItems,
} from './graph/graphAnalyticsToggles'
import {
  atomsToDecimal,
  computeSeedLabel,
  extractPidFromText,
  labelPartsToMode,
  money,
  modeToLabelParts,
  pct,
  type LabelPart,
} from './graph/graphPageHelpers'
import { useGraphConnections } from './graph/useGraphConnections'
import { useGraphFocusMode } from './graph/useGraphFocusMode'
import { useGraphPageStorage } from './graph/useGraphPageStorage'
import { DEV_GRAPH_DOUBLE_TAP_DELAY_MS } from '../constants/timing'
import { installGraphDevHooks } from './graph/graphDevHooks'
import { useGraphPageOptions } from './graph/useGraphPageOptions'
import { useGraphPageWatchers } from './graph/useGraphPageWatchers'
import { readQueryString, toLocationQueryRaw } from '../router/query'
import { useRouteHydrationGuard } from '../composables/useRouteHydrationGuard'
import { effectiveApiMode } from '../api/apiMode'

const route = useRoute()
const router = useRouter()

const { isApplying: applyingRouteQuery, isActive: isGraphRoute, run: withRouteHydration } =
  useRouteHydrationGuard(route, '/graph')

function updateRouteQuery(patch: Record<string, unknown>) {
  // Avoid calling router.replace after the user navigated away (prevents double navigation/flicker).
  if (!isGraphRoute.value) return
  const query: Record<string, unknown> = { ...route.query }
  for (const [k, v] of Object.entries(patch)) {
    const s = typeof v === 'string' ? v.trim() : v
    if (s === '' || s === null || s === undefined) delete query[k]
    else query[k] = v
  }
  void router.replace({ query: toLocationQueryRaw(query) })
}

const cyRoot = ref<HTMLElement | null>(null)
let cy: Core | null = null
const getCy = () => cy

const { statuses, layoutOptions } = useGraphPageOptions()

const setCy = (next: Core | null) => {
  cy = next

  // Dev-only E2E hook: lets Playwright tap a node deterministically.
  if (import.meta.env.DEV) {
    installGraphDevHooks(next, DEV_GRAPH_DOUBLE_TAP_DELAY_MS)
  }
}

const drawerTab = ref<DrawerTab>('summary')
const drawerEq = ref<string>('ALL')

const analyticsEq = computed(() => {
  const key = String(drawerEq.value || '').trim().toUpperCase()
  return key === 'ALL' ? null : key
})

const analytics = ref<AnalyticsToggles>({ ...DEFAULT_ANALYTICS_TOGGLES })

const seedLabel = computed(() => {
  return computeSeedLabel(participants.value)
})

const eq = ref<string>('')  // Will be auto-selected to primary equivalent after loadData()
const statusFilter = ref<string[]>(['active', 'frozen', 'closed'])
const threshold = ref<string>(DEFAULT_THRESHOLD)

function syncFromRouteQuery() {
  // Avoid mutating state when this component is being navigated away from.
  if (!isGraphRoute.value) return
  withRouteHydration(() => {
    const nextEq = readQueryString(route.query.equivalent).trim().toUpperCase()
    const nextThr = readQueryString(route.query.threshold).trim()
    if (nextEq) eq.value = nextEq === 'ALL' ? '' : nextEq
    if (nextThr) threshold.value = nextThr
  })
}

watch(
  () => [route.query.equivalent, route.query.threshold],
  () => syncFromRouteQuery(),
  { immediate: true },
)

watch(eq, (v) => {
  if (applyingRouteQuery.value) return
  updateRouteQuery({ equivalent: v || '' })
})
watch(threshold, (v) => {
  if (applyingRouteQuery.value) return
  updateRouteQuery({ threshold: String(v || '').trim() })
})

const typeFilter = ref<string[]>(['person', 'business'])
const minDegree = ref<number>(0)

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

const toolbarTab = ref<'filters' | 'display'>('filters')

const isRealMode = computed(() => effectiveApiMode() === 'real')
const selected = ref<SelectedInfo | null>(null)

const MAX_AUTO_RENDER_NODES = 1500
const MAX_AUTO_RENDER_EDGES = 8000

const renderOverride = ref(false)
const cyInitialized = ref(false)

const zoom = ref<number>(1)

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
  filteredTrustlines,
  precisionByEq,
  incidentRatioByPid: incidentRatioByPidAll,
  participantByPid,
  loadData,
  refreshSnapshotForEq,
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

const { setFocusRoot, ensureFocusRootPid, useSelectedForFocus, clearFocusMode, canUseSelectedForFocus } = useGraphFocusMode({
  selected,
  focusPid,
  searchQuery,
  focusMode,
  focusRootPid,
  extractPidFromText,
})

const selectedBalanceRows = graphAnalytics.selectedBalanceRows

// Note: we keep split counterparties (creditors vs debtors) as the primary UI.

const selectedCounterpartySplit = graphAnalytics.selectedCounterpartySplit

const selectedConcentration = graphAnalytics.selectedConcentration

const netDistribution = graphAnalytics.netDistribution

const selectedRank = graphAnalytics.selectedRank

const selectedCapacity = graphAnalytics.selectedCapacity

const selectedActivity = graphAnalytics.selectedActivity

const selectedCycles = graphAnalytics.selectedCycles

const incidentRatioByPid = computed(() => (showIncidents.value ? incidentRatioByPidAll.value : new Map<string, number>()))

const { restore: restoreStorage } = useGraphPageStorage({
  showLegend,
  layoutSpacing,
  toolbarTab,
  drawerEq,
  analytics,
})

const graphViz = useGraphVisualization({
  cyRoot,
  getCy,
  setCy,

  threshold,

  typeFilter,
  minDegree,
  hideIsolates,
  showIncidents,

  participants,
  filteredTrustlines,
  incidentRatioByPid,

  selected,
  drawerOpen,
  drawerTab,

  searchQuery,
  focusPid,

  focusMode,
  focusRootPid,
  focusDepth,
  setFocusRoot,

  showLabels,
  labelModeBusiness,
  labelModePerson,
  autoLabelsByZoom,
  minZoomLabelsAll,
  minZoomLabelsPerson,

  zoom,

  layoutName,
  layoutSpacing,

  activeCycleKey,
  activeConnectionKey,

  extractPidFromText,
})

const {
  selectedConnectionsIncoming,
  selectedConnectionsOutgoing,
  selectedConnectionsIncomingPaged,
  selectedConnectionsOutgoingPaged,

  connectionsPageSize,
  connectionsIncomingPage,
  connectionsOutgoingPage,

  onConnectionRowClick,
} = useGraphConnections({
  getCy,
  participantByPid,
  selected,
  threshold,
  activeConnectionKey,

  clearConnectionHighlight: graphViz.clearConnectionHighlight,
  highlightConnection: graphViz.highlightConnection,
  goToPid: graphViz.goToPid,
})

/*
 * NOTE: Legacy Cytoscape/build/layout/search code used to live below.
 * It is now delegated to `useGraphVisualization()`.
 * The in-file implementation has been removed to keep `GraphPage.vue` focused.
 */

onMounted(async () => {
  restoreStorage()

  await reloadAll()
})

onBeforeUnmount(() => {
  graphViz.destroyCy()
})

const rawNodesCount = computed(() => (participants.value || []).length)
const rawEdgesCount = computed(() => (filteredTrustlines.value || []).length)

const isTooLargeToAutoRender = computed(() => {
  return rawNodesCount.value > MAX_AUTO_RENDER_NODES || rawEdgesCount.value > MAX_AUTO_RENDER_EDGES
})

function ensureCyInitialized() {
  if (cyInitialized.value) return
  if (isTooLargeToAutoRender.value && !renderOverride.value) return
  graphViz.initCy()
  cyInitialized.value = true
}

function renderAnyway() {
  renderOverride.value = true
  ensureCyInitialized()
}

async function reloadAll() {
  await loadData()
  // If data got smaller after filters/focus changes, auto-init.
  ensureCyInitialized()
  // If graph is already initialized, just rebuild it.
  graphViz.rebuildGraph({ fit: true })
}

useGraphPageWatchers({
  isRealMode,
  eq,
  statusFilter,
  threshold,
  showIncidents,
  hideIsolates,
  typeFilter,
  minDegree,
  focusMode,
  focusDepth,
  focusRootPid,
  ensureFocusRootPid,
  refreshForFocusMode,
  refreshSnapshotForEq,
  selected,
  clearingCycles,
  showLabels,
  labelModeBusiness,
  labelModePerson,
  autoLabelsByZoom,
  minZoomLabelsAll,
  minZoomLabelsPerson,
  searchQuery,
  focusPid,
  zoom,
  layoutName,
  layoutSpacing,
  graphViz,
})

const stats = computed(() => {
  if (isTooLargeToAutoRender.value && !renderOverride.value) {
    return { nodes: rawNodesCount.value, edges: rawEdgesCount.value, bottlenecks: 0 }
  }
  const { nodes, edges } = graphViz.buildElements()
  const bottlenecks = edges.filter((e) => e.data?.bottleneck === 1).length
  return { nodes: nodes.length, edges: edges.length, bottlenecks }
})
</script>

<template>
  <el-card class="geoCard">
    <template #header>
      <div class="hdr">
        <TooltipLabel
          :label="t('graph.title')"
          tooltip-key="nav.graph"
        />
        <div class="hdr__right">
          <div class="hdr__controls">
            <el-button
              size="small"
              @click="graphViz.fit"
            >
              {{ t('graph.navigate.fit') }}
            </el-button>
            <el-button
              size="small"
              @click="graphViz.runLayout"
            >
              {{ t('graph.navigate.relayout') }}
            </el-button>

            <div class="hdrZoom">
              <TooltipLabel
                class="hdrZoom__label"
                :label="t('graph.navigate.zoom')"
                tooltip-key="graph.zoom"
              />
              <el-slider
                v-model="zoom"
                :min="0.1"
                :max="3"
                :step="0.05"
                class="hdrZoom__slider"
              />
            </div>
          </div>

          <div class="hdr__stats">
          <el-tag type="info">
            {{ seedLabel }}
          </el-tag>
          <el-tag type="info">
            {{ t('graph.stats.nodes') }}: {{ stats.nodes }}
          </el-tag>
          <el-tag type="info">
            {{ t('graph.stats.edges') }}: {{ stats.edges }}
          </el-tag>
          <el-tag
            v-if="stats.bottlenecks"
            type="danger"
          >
            {{ t('graph.stats.bottlenecks') }}: {{ stats.bottlenecks }}
          </el-tag>
          </div>
        </div>
      </div>
    </template>

    <LoadErrorAlert
      v-if="error"
      :title="error"
      :busy="loading"
      @retry="reloadAll"
    />

    <el-alert
      v-else-if="isTooLargeToAutoRender && !renderOverride"
      type="warning"
      show-icon
      :closable="false"
      class="mb"
      :title="t('graph.guard.title', { nodes: rawNodesCount, edges: rawEdgesCount })"
    >
      <template #default>
        <div class="guardRow">
          <div class="guardHint">{{ t('graph.guard.hint', { maxNodes: MAX_AUTO_RENDER_NODES, maxEdges: MAX_AUTO_RENDER_EDGES }) }}</div>
          <el-button
            size="small"
            type="primary"
            @click="renderAnyway"
          >
            {{ t('graph.guard.renderAnyway') }}
          </el-button>
        </div>
      </template>
    </el-alert>

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
      v-model:focus-mode="focusMode"
      v-model:focus-depth="focusDepth"
      :available-equivalents="availableEquivalents"
      :statuses="statuses"
      :layout-options="layoutOptions"
      :fetch-suggestions="graphViz.querySearchParticipants"
      :on-focus-search="graphViz.focusSearch"
      :can-find="graphViz.canFind.value"
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
        data-testid="graph-cy"
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
    :is-cycle-active="graphViz.isCycleActive"
    :toggle-cycle-highlight="graphViz.toggleCycleHighlight"
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
  flex-wrap: wrap;
  justify-content: flex-end;
}

.hdr__controls {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

/* Element Plus adds default spacing via .el-button + .el-button { margin-left: 12px }.
   In this row we rely on flex gap for consistent spacing. */
.hdr__controls :deep(.el-button + .el-button) {
  margin-left: 0;
}

.hdr__stats {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.hdrZoom {
  display: flex;
  gap: 8px;
  align-items: center;
}

.hdrZoom__label {
  color: var(--el-text-color-secondary);
}

.hdrZoom__slider {
  width: 140px;
}

.guardRow {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.guardHint {
  color: var(--el-text-color-secondary);
  font-size: var(--geo-font-size-sub);
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
