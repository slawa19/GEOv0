<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useGraphData } from '../composables/useGraphData'
import { useGraphAnalytics } from '../composables/useGraphAnalytics'
import { graphSelectionAnnouncement, useGraphVisualization } from '../composables/useGraphVisualization'
import type { DrawerTab, GraphElementOption, GraphRebuildOptions, LabelMode, SelectedInfo } from '../composables/useGraphVisualization'
import {
  DEFAULT_FOCUS_DEPTH,
  DEFAULT_LAYOUT_SPACING,
  DEFAULT_THRESHOLD,
  MIN_ZOOM_LABELS_ALL,
  MIN_ZOOM_LABELS_PERSON,
} from '../constants/graph'
import TooltipLabel from '../ui/TooltipLabel.vue'
import LoadErrorAlert from '../ui/LoadErrorAlert.vue'
import { locale, t } from '../i18n'
import GraphAnalyticsDrawer from './graph/GraphAnalyticsDrawer.vue'
import GraphLegend from './graph/GraphLegend.vue'
import GraphFiltersToolbar from './graph/GraphFiltersToolbar.vue'
import {
  createDebouncedGraphElementSearch,
  extractPidFromText,
  graphElementOptionsForSearch,
  guardedGraphSearchCacheAction,
  labelPartsToMode,
  money,
  modeToLabelParts,
  reloadGraphView,
  syncGraphCoreForView,
  waitForLatestPendingGraphLoad,
  type LabelPart,
} from './graph/graphPageHelpers'
import { useGraphConnections } from './graph/useGraphConnections'
import { useGraphFocusMode } from './graph/useGraphFocusMode'
import { useGraphPageStorage } from './graph/useGraphPageStorage'
import { useGraphPageOptions } from './graph/useGraphPageOptions'
import { useGraphPageWatchers } from './graph/useGraphPageWatchers'
import GraphKeyboardNavigator from './graph/GraphKeyboardNavigator.vue'
import { useRouteQueryFilters } from '../composables/useRouteQueryFilters'
import { normalizeEquivalentCode } from '../utils/equivalent'
import { useLatestRequest } from '../composables/useLatestRequest'

const route = useRoute()
const router = useRouter()

const cyRoot = ref<HTMLElement | null>(null)

const { statuses, layoutOptions } = useGraphPageOptions()

const drawerTab = ref<DrawerTab>('summary')
const drawerEq = ref<string>('ALL')

const analyticsEq = computed(() => {
  const key = normalizeEquivalentCode(drawerEq.value)
  return key === 'ALL' ? null : key
})

const eq = ref<string>('')  // Will be auto-selected to primary equivalent after loadData()
const statusFilter = ref<string[]>(['active', 'closed'])
const threshold = ref<string>(DEFAULT_THRESHOLD)

// The equivalent and the bottleneck threshold are linked in the URL. A route that does not carry them leaves them
// as they are: the page picks the equivalent itself once the data is in (`eqAutoSelected`), and the threshold has a default.
const { applyRoute } = useRouteQueryFilters({
  route,
  router,
  path: '/graph',
  filters: {
    equivalent: {
      model: eq,
      fromQuery: (raw) => {
        const code = normalizeEquivalentCode(raw)
        return code === 'ALL' ? '' : code
      },
      toQuery: (value) => value,
      keepWhenAbsent: true,
    },
    // The default is not written to the URL (as on Trustlines): a link carries what was chosen.
    threshold: {
      model: threshold,
      toQuery: (value) => (value.trim() === DEFAULT_THRESHOLD ? '' : value.trim()),
      keepWhenAbsent: true,
    },
  },
})
applyRoute()

const typeFilter = ref<string[]>(['person', 'business'])
const minDegree = ref<number>(0)

const showLabels = ref(true)
const labelModeBusiness = ref<LabelMode>('name')
const labelModePerson = ref<LabelMode>('name')
const autoLabelsByZoom = ref(true)
const minZoomLabelsAll = ref(MIN_ZOOM_LABELS_ALL)
const minZoomLabelsPerson = ref(MIN_ZOOM_LABELS_PERSON)

const hideIsolates = ref(true)
const showLegend = ref(false)

const layoutName = ref<'fcose' | 'grid' | 'circle'>('fcose')
const layoutSpacing = ref<number>(DEFAULT_LAYOUT_SPACING)

const toolbarTab = ref<'filters' | 'display'>('filters')

const selected = ref<SelectedInfo | null>(null)

const MAX_AUTO_RENDER_NODES = 1500
const MAX_AUTO_RENDER_EDGES = 8000

const renderOverride = ref(false)
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
  availableEquivalents,
  eqAutoSelected,
  filteredTrustlines,
  precisionByEq,
  participantByPid,
  refreshSnapshotForEq,
  refreshForFocusMode,
  invalidateDataOwnership,
  reloadCurrentView,
} = useGraphData({
  eq,
  focusMode,
  focusRootPid,
  focusDepth,
  statusFilter,
})

/**
 * Денежная ячейка графа печатается по точности эквивалента строки (F-012-7).
 * Каталог `precisionByEq` уже загружен `useGraphData`; вызывающий обязан назвать эквивалент,
 * иначе величина печатается прочерком, а не выдуманным числом знаков.
 */
function moneyByEquivalent(value: string, equivalent: unknown): string {
  return money(value, equivalent, precisionByEq.value)
}

const { metricsLoading, metricsError, selectedBalanceRows, reloadSelectedMetrics } = useGraphAnalytics({
  analyticsEq,
  selected,
})

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

const { restore: restoreStorage } = useGraphPageStorage({
  showLegend,
  layoutSpacing,
  toolbarTab,
  drawerEq,
})

const graphViz = useGraphVisualization({
  cyRoot,

  threshold,

  typeFilter,
  minDegree,
  hideIsolates,

  participants,
  filteredTrustlines,

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
  getCy: graphViz.getCy,
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

const graphEffectRequests = useLatestRequest()
let pendingGraphLoad: Promise<unknown> | null = null

onMounted(async () => {
  restoreStorage()

  await reloadAll()
})

const rawNodesCount = computed(() => (participants.value || []).length)
const rawEdgesCount = computed(() => (filteredTrustlines.value || []).length)

const isTooLargeToAutoRender = computed(() => {
  return rawNodesCount.value > MAX_AUTO_RENDER_NODES || rawEdgesCount.value > MAX_AUTO_RENDER_EDGES
})
const graphRenderGuardActive = computed(() => isTooLargeToAutoRender.value && !renderOverride.value)

function applyGraphView(rebuildOptions: GraphRebuildOptions) {
  return syncGraphCoreForView({
    guarded: graphRenderGuardActive.value,
    hasCore: () => Boolean(graphViz.getCy()),
    initialize: graphViz.initCy,
    destroy: graphViz.destroyCy,
    rebuild: graphViz.rebuildGraph,
    rebuildOptions,
  })
}

function renderAnyway() {
  renderOverride.value = true
  applyGraphView({ fit: true })
}

function reloadGraph(rebuildOptions: { fit: boolean; preserveViewport?: boolean }) {
  const request = graphEffectRequests.begin()
  const operation = reloadGraphView({
    loadData: reloadCurrentView,
    isCurrent: request.isCurrent,
    afterLoad: async () => { await nextTick() },
    applyView: applyGraphView,
    rebuildOptions,
  })
  let tracked!: Promise<boolean>
  tracked = operation.finally(() => {
    if (pendingGraphLoad === tracked) pendingGraphLoad = null
  })
  pendingGraphLoad = tracked
  return tracked
}

async function waitForPendingGraphLoad() {
  await waitForLatestPendingGraphLoad(() => pendingGraphLoad)
}

function reloadAll() {
  return reloadGraph({ fit: true })
}

function reloadDrawer() {
  // The drawer's "Refresh" refreshes what the drawer shows: the graph and the selected participant's balance rows.
  void reloadSelectedMetrics()
  return reloadGraph({ fit: false, preserveViewport: true })
}

useGraphPageWatchers({
  eq,
  statusFilter,
  threshold,
  hideIsolates,
  typeFilter,
  minDegree,
  focusMode,
  focusDepth,
  focusRootPid,
  ensureFocusRootPid,
  refreshForFocusMode,
  refreshSnapshotForEq,
  invalidateDataOwnership,
  waitForPendingGraphLoad,
  selected,
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
  graphEffectRequests,
  applyGraphView,
  graphViz,
})

const stats = computed(() => {
  if (graphRenderGuardActive.value) {
    return { nodes: rawNodesCount.value, edges: rawEdgesCount.value, bottlenecks: 0 }
  }
  const { nodes, edges } = graphViz.buildElements()
  const bottlenecks = edges.filter((e) => e.data?.bottleneck === 1).length
  return { nodes: nodes.length, edges: edges.length, bottlenecks }
})

const keyboardElementKey = ref('')
const keyboardElementQuery = ref('')
const GUARDED_KEYBOARD_QUERY_MIN = 2
const GUARDED_KEYBOARD_OPTION_LIMIT = 100
const GUARDED_KEYBOARD_DEBOUNCE_MS = 200
const guardedKeyboardElementOptions = ref<GraphElementOption[]>([])
const guardedKeyboardSearch = createDebouncedGraphElementSearch({
  delayMs: GUARDED_KEYBOARD_DEBOUNCE_MS,
  guardedQueryMin: GUARDED_KEYBOARD_QUERY_MIN,
  guardedLimit: GUARDED_KEYBOARD_OPTION_LIMIT,
  buildOptions: graphViz.graphElementOptions,
  publish: (options) => { guardedKeyboardElementOptions.value = options },
})
const keyboardElementOptions = computed(() => graphElementOptionsForSearch({
  guarded: false,
  query: keyboardElementQuery.value,
  guardedQueryMin: GUARDED_KEYBOARD_QUERY_MIN,
  guardedLimit: GUARDED_KEYBOARD_OPTION_LIMIT,
  buildOptions: graphViz.graphElementOptions,
}))
const visibleKeyboardElementOptions = computed(() => (
  graphRenderGuardActive.value ? guardedKeyboardElementOptions.value : keyboardElementOptions.value
))
let drawerReturnFocus: HTMLElement | null = null

function searchKeyboardElements(query: string) {
  keyboardElementQuery.value = query
  if (graphRenderGuardActive.value) {
    guardedKeyboardSearch.search(query)
    return
  }
  guardedKeyboardSearch.cancel()
  guardedKeyboardElementOptions.value = []
}

watch(
  [
    graphRenderGuardActive,
    participants,
    filteredTrustlines,
    threshold,
    typeFilter,
    minDegree,
    hideIsolates,
    focusMode,
    focusRootPid,
    focusDepth,
    focusPid,
    locale,
  ],
  ([guarded], [wasGuarded]) => {
    const action = guardedGraphSearchCacheAction(guarded, wasGuarded)
    if (action === 'search') guardedKeyboardSearch.search(keyboardElementQuery.value)
    if (action === 'invalidate') guardedKeyboardSearch.invalidate()
  },
)

onBeforeUnmount(guardedKeyboardSearch.cancel)

function openKeyboardElement() {
  const active = document.activeElement
  drawerReturnFocus = active instanceof HTMLElement ? active : null
  if (!graphViz.openElementDetails(keyboardElementKey.value)) drawerReturnFocus = null
}

watch(drawerOpen, async (open, wasOpen) => {
  if (open || !wasOpen || !drawerReturnFocus) return
  const target = drawerReturnFocus
  drawerReturnFocus = null
  await nextTick()
  if (target.isConnected) target.focus()
})

const graphLiveAnnouncement = computed(() => {
  if (loading.value) return t('graph.a11y.loading')
  if (error.value) return t('graph.a11y.error', { error: error.value })
  const selection = graphSelectionAnnouncement(selected.value, drawerOpen.value)
  if (selection) return selection
  return t('graph.a11y.ready', { nodes: stats.value.nodes, edges: stats.value.edges })
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
      v-else-if="graphRenderGuardActive"
      type="warning"
      show-icon
      :closable="false"
      class="mb"
      :title="t('graph.guard.title', { nodes: rawNodesCount, edges: rawEdgesCount })"
    >
      <template #default>
        <div class="guardRow">
          <div class="guardHint">
            {{ t('graph.guard.hint', { maxNodes: MAX_AUTO_RENDER_NODES, maxEdges: MAX_AUTO_RENDER_EDGES }) }}
          </div>
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
      v-model:hide-isolates="hideIsolates"
      v-model:show-legend="showLegend"
      v-model:search-query="searchQuery"
      v-model:focus-pid="focusPid"
      v-model:focus-mode="focusMode"
      v-model:focus-depth="focusDepth"
      :available-equivalents="availableEquivalents"
      :eq-auto-selected="eqAutoSelected"
      :statuses="statuses"
      :layout-options="layoutOptions"
      :fetch-suggestions="graphViz.querySearchParticipants"
      :can-find="graphViz.canFind.value"
      :focus-root-pid="focusRootPid"
      :can-use-selected-for-focus="canUseSelectedForFocus"
      @focus-search="graphViz.focusSearch"
      @use-selected-for-focus="useSelectedForFocus"
      @clear-focus="clearFocusMode"
    />

    <GraphKeyboardNavigator
      v-model="keyboardElementKey"
      :options="visibleKeyboardElementOptions"
      :busy="loading"
      :hint-id="graphRenderGuardActive ? 'graph-keyboard-guard-hint' : undefined"
      @open="openKeyboardElement"
      @search="searchKeyboardElements"
    />
    <p
      v-if="graphRenderGuardActive"
      id="graph-keyboard-guard-hint"
      class="keyboardGuardHint"
    >
      {{ t('graph.keyboard.largeGraphHint', {
        min: GUARDED_KEYBOARD_QUERY_MIN,
        limit: GUARDED_KEYBOARD_OPTION_LIMIT,
      }) }}
    </p>

    <div
      id="graph-keyboard-alternative"
      class="visuallyHidden"
    >
      {{ t('graph.a11y.alternativeHint') }}
    </div>
    <div
      class="visuallyHidden"
      role="status"
      aria-live="polite"
      aria-atomic="true"
      data-testid="graph-live-status"
    >
      {{ graphLiveAnnouncement }}
    </div>

    <div
      class="cy-wrap"
    >
      <div
        v-if="loading"
        class="cy-loading"
        aria-hidden="true"
      >
        <el-skeleton
          animated
          :rows="6"
        />
      </div>
      <GraphLegend :open="showLegend" />
      <div
        ref="cyRoot"
        class="cy"
        role="img"
        :aria-label="t('graph.a11y.canvasLabel')"
        :aria-busy="loading"
        aria-describedby="graph-keyboard-alternative"
        data-testid="graph-cy"
      />
    </div>
  </el-card>

  <GraphAnalyticsDrawer
    v-model="drawerOpen"
    v-model:tab="drawerTab"
    v-model:eq="drawerEq"
    v-model:connections-incoming-page="connectionsIncomingPage"
    v-model:connections-outgoing-page="connectionsOutgoingPage"
    :selected="selected"
    :available-equivalents="availableEquivalents"
    :money="moneyByEquivalent"
    :metrics-loading="metricsLoading"
    :metrics-error="metricsError"
    :selected-balance-rows="selectedBalanceRows"
    :selected-connections-incoming="selectedConnectionsIncoming"
    :selected-connections-outgoing="selectedConnectionsOutgoing"
    :selected-connections-incoming-paged="selectedConnectionsIncomingPaged"
    :selected-connections-outgoing-paged="selectedConnectionsOutgoingPaged"
    :connections-page-size="connectionsPageSize"
    @refresh="reloadDrawer"
    @connection-row-click="onConnectionRowClick"
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

.cy-loading {
  position: absolute;
  inset: 0;
  z-index: 2;
  padding: 16px;
  background: var(--el-bg-color-overlay);
}

.keyboardGuardHint {
  margin: -4px 0 12px;
  color: var(--el-text-color-secondary);
  font-size: var(--geo-font-size-label);
}

.cy {
  height: 100%;
  width: 100%;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  background: var(--el-bg-color-overlay);
}

.visuallyHidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>
