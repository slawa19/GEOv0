<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Core } from 'cytoscape'

import { api } from '../api'
import { assertSuccess } from '../api/envelope'
import { useGraphData } from '../composables/useGraphData'
import { useGraphAnalytics } from '../composables/useGraphAnalytics'
import { useGraphVisualization } from '../composables/useGraphVisualization'
import type { DrawerTab, LabelMode, SelectedInfo } from '../composables/useGraphVisualization'
import { throttle } from '../utils/throttle'
import { THROTTLE_GRAPH_REBUILD_MS, THROTTLE_LAYOUT_SPACING_MS } from '../constants/timing'
import {
  DEFAULT_FOCUS_DEPTH,
  DEFAULT_LAYOUT_SPACING,
  DEFAULT_THRESHOLD,
  MIN_ZOOM_LABELS_ALL,
  MIN_ZOOM_LABELS_PERSON,
} from '../constants/graph'
import TooltipLabel from '../ui/TooltipLabel.vue'
import { t } from '../i18n/en'
import GraphAnalyticsDrawer from './graph/GraphAnalyticsDrawer.vue'
import GraphLegend from './graph/GraphLegend.vue'
import GraphFiltersToolbar from './graph/GraphFiltersToolbar.vue'
import type { ClearingCycles } from './graph/graphTypes'
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
import { layoutOptions, statuses } from './graph/graphUiOptions'
import { useGraphPageStorage } from './graph/useGraphPageStorage'

const cyRoot = ref<HTMLElement | null>(null)
let cy: Core | null = null
const getCy = () => cy

const setCy = (next: Core | null) => {
  cy = next
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

const eq = ref<string>('ALL')
const statusFilter = ref<string[]>(['active', 'frozen', 'closed'])
const threshold = ref<string>(DEFAULT_THRESHOLD)

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

const toolbarTab = ref<'filters' | 'display' | 'navigate'>('filters')

const isRealMode = computed(() => (import.meta.env.VITE_API_MODE || 'mock').toString().toLowerCase() === 'real')
const selected = ref<SelectedInfo | null>(null)

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

  await loadData()

  graphViz.initCy()
})

onBeforeUnmount(() => {
  graphViz.destroyCy()
})

const throttledRebuild = throttle(() => {
  graphViz.rebuildGraph({ fit: false })
}, THROTTLE_GRAPH_REBUILD_MS)

const throttledLayoutSpacing = throttle(() => {
  graphViz.runLayout()
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
    graphViz.rebuildGraph({ fit: true })
  })()
})

watch(
  () => (selected.value && selected.value.kind === 'node' ? selected.value.pid : ''),
  (pid) => {
    graphViz.clearCycleHighlight()
    graphViz.clearConnectionHighlight()
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
      graphViz.applySelectedHighlight(pid)
    })()
  }
)

watch([showLabels, labelModeBusiness, labelModePerson, autoLabelsByZoom, minZoomLabelsAll, minZoomLabelsPerson], () => {
  graphViz.applyStyle()
  graphViz.updateLabelsForZoom()
})

watch(searchQuery, () => {
  // If user edits the query manually, clear explicit selection.
  focusPid.value = ''
  graphViz.updateSearchHighlights()
})

watch(zoom, (z) => {
  graphViz.syncZoomFromControl(z)
})

watch(layoutName, () => {
  graphViz.runLayout()
})

watch(layoutSpacing, () => {
  throttledLayoutSpacing()
})

const stats = computed(() => {
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
      :fetch-suggestions="graphViz.querySearchParticipants"
      :on-focus-search="graphViz.focusSearch"
      :on-fit="graphViz.fit"
      :on-relayout="graphViz.runLayout"
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
