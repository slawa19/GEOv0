<script setup lang="ts">
import TopBar from './TopBar.vue'
import BottomBar from './BottomBar.vue'
import ActionBar from './ActionBar.vue'
import SystemBalanceBar from './SystemBalanceBar.vue'
import ManualPaymentPanel from './ManualPaymentPanel.vue'
import WindowShell from './WindowShell.vue'
import TrustlineManagementPanel from './TrustlineManagementPanel.vue'
import EdgeDetailPopup from './EdgeDetailPopup.vue'
import ClearingPanel from './ClearingPanel.vue'
import EdgeTooltip from './EdgeTooltip.vue'
import LabelsOverlayLayers from './LabelsOverlayLayers.vue'
import NodeCardOverlay from './NodeCardOverlay.vue'
import DevPerfOverlay from './DevPerfOverlay.vue'
import ErrorToast from './ErrorToast.vue'
import SuccessToast from './SuccessToast.vue'
import InteractHistoryLog from './InteractHistoryLog.vue'
import GraphNavigator from './GraphNavigator.vue'
import Legend from './Legend.vue'
import RealMetricsPanel from './RealMetricsPanel.vue'

import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

 import { provideTopBarContext, type TopBarContext } from '../composables/useTopBarContext'

 import type { InteractPhase } from '../composables/useInteractMode'
import { canActOnTrustlineFigures } from '../composables/interact/trustlinesSourceState'
import { useSelectedTrustlineLine } from '../composables/useSelectedTrustlineLine'
import { provideActivePanelState } from '../composables/useActivePanelState'
import {
  participantDisplayIdFor,
  provideParticipantNameOf,
  provideShownScenarioId,
  shownScenarioIdOf,
} from '../composables/useParticipantDisplay'
 import type { Point } from '../types/layout'
 import { useSimulatorStorage } from '../composables/usePersistedSimulatorPrefs'
 import { useUiTheme } from '../composables/useUiTheme'
 import { toLower } from '../utils/stringHelpers'
 import { canonicalEquivalent } from '../config/equivalents'
 import { useReducedMotionPreference } from '../composables/useReducedMotionPreference'

// TD-1: all localStorage access is delegated to this composable.
const simulatorStorage = useSimulatorStorage()
const reducedMotion = useReducedMotionPreference()

// 039 B3: which theme is shown, and what a switch writes back - `useUiTheme` (URL, stored choice, default).
const { uiTheme, setUiTheme, onPopState: syncThemeFromUrl } = useUiTheme(simulatorStorage)

onMounted(() => {
  // Allow browser back/forward to update theme without reload.
  window.addEventListener('popstate', syncThemeFromUrl)

  // E1 (P0): WM geometry is driven by DS tokens on the host element.
  // Safe in tests: readOverlayGeometryPx() falls back to defaults.
  {
    const host = hostEl.value
    const geo = readOverlayGeometryPx(host)
    wm.setGeometry({
      clampPadPx: geo.wmClampPadPx,
      dockedRightInsetPx: geo.wmClampPadPx,
      dockedRightTopPx: geo.hudStackHeightPx,

      anchorOffsetXPx: geo.wmAnchorOffsetXPx,
      anchorOffsetYPx: geo.wmAnchorOffsetYPx,
      cascadeStepPx: geo.wmCascadeStepPx,

      interactPanelMinWidthPx: geo.wmInteractMinWidthPx,
      interactPanelMinHeightPx: geo.wmInteractMinHeightPx,
      interactPanelPreferredWidthTrustlinePx: geo.wmInteractPreferredWidthTrustlinePx,
      interactPanelPreferredWidthPaymentPx: geo.wmInteractPreferredWidthPaymentPx,
      interactPanelPreferredWidthWidePx: geo.wmInteractPreferredWidthWidePx,
      interactPanelPreferredHeightLoadingPx: geo.wmInteractPreferredHeightLoadingPx,
      interactPanelPreferredHeightConfirmPx: geo.wmInteractPreferredHeightConfirmPx,
      interactPanelPreferredHeightPickingPx: geo.wmInteractPreferredHeightPickingPx,

      edgeDetailMinWidthPx: geo.wmEdgeDetailMinWidthPx,
      edgeDetailMinHeightPx: geo.wmEdgeDetailMinHeightPx,
      edgeDetailPreferredWidthPx: geo.wmEdgeDetailPreferredWidthPx,
      edgeDetailPreferredHeightPx: geo.wmEdgeDetailPreferredHeightPx,

      nodeCardMinWidthPx: geo.wmNodeCardMinWidthPx,
      nodeCardMinHeightPx: geo.wmNodeCardMinHeightPx,
      nodeCardPreferredWidthPx: geo.wmNodeCardPreferredWidthPx,
      nodeCardPreferredHeightPx: geo.wmNodeCardPreferredHeightPx,

      groupZInspectorBase: geo.wmGroupZInspectorBase,
      groupZInteractBase: geo.wmGroupZInteractBase,
    })
  }
})

onUnmounted(() => {
  window.removeEventListener('popstate', syncThemeFromUrl)
})

import type { GraphLink } from '../types'
import type { BottleneckTarget } from '../api/simulatorTypes'

import { useSimulatorApp } from '../composables/useSimulatorApp'
import { computeNodeEdgeStats } from '../composables/useSelectedNodeEdgeStats'
 import { emptyToNull, emptyToNullString } from '../utils/valueFormat'
 import { keyEdge } from '../utils/edgeKey'
 import { startRetryOffered } from '../utils/runErrorClassification'
 import { useWindowManager } from '../composables/windowManager/useWindowManager'
 import type { WindowInstance } from '../composables/windowManager/types'
 import { isInteractPanelWindow, isNodeCardWindow, isEdgeDetailWindow } from '../composables/windowManager/types'
 import { useWmEdgeDetail } from '../composables/useWmEdgeDetail'
 import { useWindowController } from '../composables/useWindowController'
import { resolveOverlayDockStyle, resolveWindowSurfaceDescriptor } from '../ui-kit/overlaySurfaceCatalog'
import {
  createMeasuredPublishedGeometryValue,
  DEFAULT_VIEWPORT_FALLBACK_HEIGHT_PX,
  DEFAULT_VIEWPORT_FALLBACK_WIDTH_PX,
  readOverlayGeometryPx,
} from '../ui-kit/overlayGeometry'

function readWindowViewportFallback(): { width: number; height: number } {
  try {
    const w = typeof window !== 'undefined' ? window : null
    const width = Number(w?.innerWidth)
    const height = Number(w?.innerHeight)
    return {
      width: Number.isFinite(width) && width > 0 ? width : DEFAULT_VIEWPORT_FALLBACK_WIDTH_PX,
      height: Number.isFinite(height) && height > 0 ? height : DEFAULT_VIEWPORT_FALLBACK_HEIGHT_PX,
    }
  } catch {
    return { width: DEFAULT_VIEWPORT_FALLBACK_WIDTH_PX, height: DEFAULT_VIEWPORT_FALLBACK_HEIGHT_PX }
  }
}

const wm = useWindowManager()
const wmEdgeDetail = useWmEdgeDetail()

let windowController: ReturnType<typeof useWindowController> | null = null
let getHostEl = (): HTMLElement | null => null

function uiCloseTopmostInspectorWindow(): 'edge-detail' | 'node-card' | null {
  return windowController?.uiCloseTopmostInspectorWindow() ?? null
}

function uiOpenOrUpdateEdgeDetail(o: { fromPid: string; toPid: string; anchor: Point }) {
  if (windowController) {
    windowController.uiOpenOrUpdateEdgeDetail(o)
    return
  }
  queueMicrotask(() => windowController?.uiOpenOrUpdateEdgeDetail(o))
}

function uiOpenOrUpdateNodeCard(o: { nodeId: string; anchor: Point | null }) {
  // 043 S2 (decision 3; review C2): opening a participant's card is a manual act - the double click, "Inspect graph",
  // the dev hook all come here - and it pauses even when the participant is already the selected one.
  void realActions.pauseForManualAction()
  if (windowController) {
    windowController.uiOpenOrUpdateNodeCard(o)
    return
  }
  queueMicrotask(() => windowController?.uiOpenOrUpdateNodeCard(o))
}

/**
 * Analytics overlay surface visibility (spec 007, T705).
 *
 * Kept here, next to the theme, and persisted through the `useSimulatorStorage` `read01`/`write01`
 * pair (the same as the «Подробнее» flag) — no new store and no new preference layer. `flush: 'sync'`:
 * the flag must reach storage before a reload can race it.
 */
const analyticsPanelOpen = ref(simulatorStorage.readAnalyticsPanelOpen() ?? false)

watch(
  analyticsPanelOpen,
  (open) => {
    simulatorStorage.writeAnalyticsPanelOpen(open)
  },
  { flush: 'sync' },
)

function toggleAnalyticsPanel(): void {
  analyticsPanelOpen.value = !analyticsPanelOpen.value
}

/**
 * 043 S1: the one "Подробнее" toggle. Off by default (a first visit, or a browser that never chose); remembered in
 * this browser through the same `read01`/`write01` pair as the analytics flag above, and applied without a reload.
 * Every service control of the two bars is rendered only while it is on - and so is the analytics dock, whose toggle
 * lives behind it: a remembered open dock does not reappear (nor poll) on a screen that has no control to close it.
 */
const detailsOpen = ref(simulatorStorage.readDetailsOpen() ?? false)

watch(
  detailsOpen,
  (open) => {
    simulatorStorage.writeDetailsOpen(open)
  },
  { flush: 'sync' },
)

function toggleDetails(): void {
  detailsOpen.value = !detailsOpen.value
}

const app = useSimulatorApp({
  uiCloseTopmostInspectorWindow,
  uiOpenOrUpdateEdgeDetail,
  uiOpenOrUpdateNodeCard,
  uiIsNodeCardOpen: () => wm.windows.value.some((w) => w.type === 'node-card'),
  optionalFxEnabled: () => !reducedMotion.value,
  isAnalyticsPanelOpen: () => detailsOpen.value && analyticsPanelOpen.value,
})

// MVP safety: ensure viewport isn't 0×0 even before `.root` ref is available.
wm.setViewport(readWindowViewportFallback())

let viewportRo: ResizeObserver | null = null
let topStackGeometryRo: ResizeObserver | null = null
let bottomStackGeometryRo: ResizeObserver | null = null
let topStackGeometryPublisher: ReturnType<typeof createMeasuredPublishedGeometryValue> | null = null
let bottomStackGeometryPublisher: ReturnType<typeof createMeasuredPublishedGeometryValue> | null = null

function readHostViewport(host: HTMLElement | null): { width: number; height: number } {
  try {
    const rect = host?.getBoundingClientRect?.()
    const w = rect?.width ?? 0
    const h = rect?.height ?? 0
    if (w > 0 && h > 0) return { width: w, height: h }
    return readWindowViewportFallback()
  } catch {
    return readWindowViewportFallback()
  }
}

function queryTopStackElement(): HTMLElement | null {
  const host = getHostEl()
  const el = host?.querySelector('.ds-ov-top')
  return el instanceof HTMLElement ? el : null
}

function queryBottomStackElement(): HTMLElement | null {
  const host = getHostEl()
  if (!host) return null

  const candidates = host.querySelectorAll('.ds-ov-bottom')
  for (const candidate of candidates) {
    if (!(candidate instanceof HTMLElement)) continue
    if (candidate.classList.contains('sar-interact-history-overlay')) continue
    return candidate
  }

  return null
}

function readPublishedStackHeightPx(el: HTMLElement | null): number | null {
  const height = Number(el?.getBoundingClientRect?.().height ?? 0)
  if (!Number.isFinite(height) || !(height > 0)) return null
  return Math.round(height)
}

/** 043 S2 (fix-delta 3): the open interact window was placed by the action bar (see `followTopStackWithRightRailAnchor`). */
let rightRailAnchorActive = false

function applyPublishedTopStackHeightPx(nextPx: number): void {
  const host = getHostEl()
  if (!host) return

  host.style.setProperty('--ds-hud-stack-height', `${nextPx}px`)
  wm.setGeometry({ dockedRightTopPx: nextPx })
  followTopStackWithRightRailAnchor()
  wm.reclampAll()
}

/**
 * 043 S2 (fix-delta 3): a window of the action bar sits in the right rail UNDER the top stack - and the stack grows when
 * "Подробнее" opens after the window did. The anchor is taken again from the live stack whenever this publisher sees
 * the stack change; the window controller passes a changed anchor to the window manager, which moves the window and
 * clamps it inside the viewport. Only the action bar's anchor follows: a node card's or a line's stays where it was put.
 */
function followTopStackWithRightRailAnchor(): void {
  if (!rightRailAnchorActive || !wmPanelOpenAnchor.value) return
  const next = getActionBarAnchor()
  const cur = wmPanelOpenAnchor.value
  if (next && (next.x !== cur.x || next.y !== cur.y)) wmPanelOpenAnchor.value = next
}

function applyPublishedBottomStackHeightPx(nextPx: number): void {
  const host = getHostEl()
  if (!host) return

  host.style.setProperty('--ds-hud-bottom-stack-height', `${nextPx}px`)
}

function bindOverlayGeometryPublishers(): void {
  const host = getHostEl()
  if (!host || typeof ResizeObserver === 'undefined') return

  resetOverlayGeometryPublishers()

  const fallback = readOverlayGeometryPx(host)

  topStackGeometryPublisher = createMeasuredPublishedGeometryValue(
    fallback.hudStackHeightPx,
    applyPublishedTopStackHeightPx,
  )
  bottomStackGeometryPublisher = createMeasuredPublishedGeometryValue(
    fallback.hudBottomStackHeightPx,
    applyPublishedBottomStackHeightPx,
  )

  const topEpoch = topStackGeometryPublisher.nextEpoch()
  const bottomEpoch = bottomStackGeometryPublisher.nextEpoch()
  const topEl = queryTopStackElement()
  const bottomEl = queryBottomStackElement()

  topStackGeometryPublisher.publish(readPublishedStackHeightPx(topEl), topEpoch)
  bottomStackGeometryPublisher.publish(readPublishedStackHeightPx(bottomEl), bottomEpoch)

  topStackGeometryRo?.disconnect()
  bottomStackGeometryRo?.disconnect()

  if (topEl) {
    topStackGeometryRo = new ResizeObserver(() => {
      topStackGeometryPublisher?.publish(readPublishedStackHeightPx(topEl), topEpoch)
    })
    topStackGeometryRo.observe(topEl)
  }

  if (bottomEl) {
    bottomStackGeometryRo = new ResizeObserver(() => {
      bottomStackGeometryPublisher?.publish(readPublishedStackHeightPx(bottomEl), bottomEpoch)
    })
    bottomStackGeometryRo.observe(bottomEl)
  }
}

function resetOverlayGeometryPublishers(): void {
  topStackGeometryRo?.disconnect()
  bottomStackGeometryRo?.disconnect()
  topStackGeometryRo = null
  bottomStackGeometryRo = null

  topStackGeometryPublisher?.reset()
  bottomStackGeometryPublisher?.reset()
  topStackGeometryPublisher = null
  bottomStackGeometryPublisher = null
}

const {
  apiMode,

  // flags
  isDemoFixtures,
  isLiveUi,
  isTestMode,
  isWebDriver,
  isE2eScreenshots,

  // real mode
  real,
  realActions,
  admin,

  // state + prefs
  state,
  eq,
  scene,
  layoutMode,
  quality,
  labelsLod,

  sceneEq,
  equivalentOptions,

  interact,

  // derived interact UI helpers
  isInteractPickingPhase,

  // env
  gpuAccelLikely,

  // refs
  dataPalette,
  resolveVizColors,
  hostEl,
  canvasEl,
  fxCanvasEl,
  dragPreviewEl,

  // derived ui
  overlayLabelScale,
  showResetView,

  // dev / diagnostics
  showPerfOverlay,
  perf,

  e2e,

  // 042 T4205: the offline effects workbench
  playlistWorkbench,

  // selection + overlays
  hoveredEdge,
  clearHoveredEdge,
  addActiveEdge,
  edgeTooltipStyle: calcEdgeTooltipStyle,
  getNodeScreenCenter,

  // pinning
  isNodePinned,
  pinNode,
  unpinNode,

  // handlers
  onCanvasClick,
  onCanvasDblClick,
  onCanvasPointerDown,
  onCanvasPointerMove,
  onCanvasPointerUp,
  onCanvasWheel,

  // labels
  labelNodes,
  floatingLabelsViewFx,
  worldToCssTranslateNoScale,

  // analytics overlay surface (spec 007)
  analytics,

  // helpers for template
  getNodeById,
  focusOnEdge,
  resetView,
} = app

getHostEl = () => hostEl.value

/** 039 slice B: the equivalent of the snapshot ON SCREEN - the legend's title. Never the selector's pending choice. */
const shownSnapshotEquivalent = computed(() => canonicalEquivalent(state.snapshot?.equivalent))

// 039 slice B: data and FX colours are read at the app's real root - once it is mounted, and again after a theme
// change has reached the DOM (`flush: 'post'`: the `data-theme` attribute is already the new one). Then one frame.
onMounted(() => resolveVizColors())
watch(uiTheme, () => resolveVizColors(), { flush: 'post' })

function nodeEdgeStatsFor(nodeId: string): ReturnType<typeof computeNodeEdgeStats> | null {
  const snapshot = state.snapshot
  if (!snapshot || !nodeId) return null
  return computeNodeEdgeStats(snapshot, nodeId)
}

const interactPhase = computed<InteractPhase>(() => interact.mode.phase.value as InteractPhase)

// MUST MP-0: canonical tri-state wiring for Manual Payment panel.
// `paymentToTargetIds` must be `undefined` strictly while routes are loading
// (trustlines fetch OR payment-targets fetch).
const trustlinesLoading = computed(() => interact.mode.trustlinesLoading.value)
const paymentTargetsLoading = computed(() => interact.mode.paymentTargetsLoading.value)
const routesLoading = computed(() => trustlinesLoading.value || paymentTargetsLoading.value)
const paymentToTargetIds = computed<Set<string> | undefined>(() =>
  routesLoading.value ? undefined : interact.mode.paymentToTargetIds.value,
)
const trustlines = computed(() => interact.mode.trustlines.value)
const trustlinesLastError = computed(() => interact.mode.trustlinesLastError?.value ?? null)
const paymentTargetsLastError = computed(() => interact.mode.paymentTargetsLastError?.value ?? null)
const paymentTargetsMaxHops = computed(() => interact.mode.paymentTargetsMaxHops)

// LOW (L2): avoid calling a function directly from template.
// This also gives Vue a chance to cache style until hoveredEdge/host changes.
const edgeTooltipStyle = computed(() => calcEdgeTooltipStyle())

// The call PROVIDES the panel state to the subtree (`provide`); its return value is not needed here.
provideActivePanelState(interactPhase)

// Components that PRINT a participant id remove the namespace of this scenario from it (`utils/participantDisplayId.ts`).
const shownScenarioId = computed(() => shownScenarioIdOf(real.runStatus, real.selectedScenarioId))
provideShownScenarioId(shownScenarioId)
// 044 `T4405`: and show a run's participant by its name (its id is an identity of the run).
const participantNameOf = (pid: string) => getNodeById(pid)?.name ?? null
provideParticipantNameOf(participantNameOf)
const showPid = participantDisplayIdFor(() => shownScenarioId.value, participantNameOf)

/**
 * Controls which UI to show in `editing-trustline` phase:
 * - `true`  → TrustlineManagementPanel (full editor)
 * - `false` → EdgeDetailPopup (quick info)
 */
const useFullTrustlineEditor = ref(false)

// Legacy panels were removed; interact panels are WM-only.

// E2 (P0): consolidate WM bridging watchers in a single controller composable.
const ctl = useWindowController({
  apiMode,
  isLiveUi,
  interactPhase,
  isFullEditor: useFullTrustlineEditor,
  interactState: interact.mode.state,
  interactMode: interact.mode,
  wm,
  wmEdgeDetail,
  getNodeScreenCenter,
})
windowController = ctl

const { wmEdgePopupAnchor, wmPanelOpenAnchor, uiCloseEdgeDetailWindow, onGlobalKeydown } = ctl

function wmSurfaceDescriptorFor(win: WindowInstance) {
  return resolveWindowSurfaceDescriptor(win, {
    getNodeName: (nodeId) => getNodeById(nodeId)?.name ?? null,
    showPid,
  })
}

// KeepAlive edge-detail: effective props (frozen when keepAlive, live otherwise).
 const wmEdgeDetailEffectiveLink = computed(() => {
   if (wmEdgeDetail.state.value === 'keepAlive' && wmEdgeDetail.frozenLink.value != null) return wmEdgeDetail.frozenLink.value
   return interactSelectedLink.value
 })
 const wmEdgeDetailEffectivePhase = computed<InteractPhase>(() => {
   if (wmEdgeDetail.state.value === 'keepAlive') return 'editing-trustline' as InteractPhase
   return interactPhase.value
 })
 const wmEdgeDetailEffectiveBusy = computed(() => {
   if (wmEdgeDetail.state.value === 'keepAlive') return true // disable action buttons while parent flow is active
   return interact.mode.busy.value
 })

 function wmEdgeDetailEffectiveState(win: WindowInstance) {
   const live = interact.mode.state
   if (!isEdgeDetailWindow(win)) return live

   // IMPORTANT: when edge-detail is kept alive (or otherwise decoupled from the live FSM),
   // the window MUST render its own frozen context from WindowManager `win.data`.
   if (wmEdgeDetail.state.value === 'keepAlive') {
     const fromPid = String(win.data.fromPid ?? '')
     const toPid = String(win.data.toPid ?? '')
     const edgeKey = String(win.data.edgeKey ?? '') || (fromPid && toPid ? keyEdge(fromPid, toPid) : null)
     return {
      ...live,
      fromPid: fromPid || null,
      toPid: toPid || null,
      selectedEdgeKey: edgeKey,
    }
  }

  return live
}

function isWmInteractPanelWindow(win: WindowInstance, panel: 'payment' | 'trustline' | 'clearing'): boolean {
  return isInteractPanelWindow(win) && win.data.panel === panel
}

function wmInteractPanelPhase(win: WindowInstance): InteractPhase {
  if (!isInteractPanelWindow(win)) return 'idle'
  return String(win.data.phase ?? 'idle') as InteractPhase
}

/**
 * 043 S1 (review, class 1): the unit the edge popup prints. Kept alive, it shows frozen figures - in the equivalent
 * captured with them, never in the live scene's.
 */
const wmEdgeDetailEffectiveUnit = computed(() =>
  wmEdgeDetail.state.value === 'keepAlive' ? (wmEdgeDetail.frozenEquivalent.value ?? '') : sceneEq.value,
)

/**
 * 043 S1 (review, class 1): a replaced scene context - another equivalent, another scenario, another run - releases
 * the kept-alive edge popup: its frozen line belongs to the scene that is gone. The payment panel the person is in
 * stays; it is live (its figures, its unit and the equivalent it submits in follow the scene on screen).
 */
watch(
  () => [sceneEq.value, shownScenarioId.value, real.runId] as const,
  () => {
    if (wmEdgeDetail.state.value !== 'keepAlive') return
    wmEdgeDetail.releaseKeepAlive()
    wmEdgeDetail.applyToWindowManager(wm, { closeReason: 'programmatic' })
  },
)

 const topBarCtx: TopBarContext = {
   apiMode,
   isTestMode,

   uiTheme,

   loadingScenarios: computed(() => real.loadingScenarios),
   scenarios: computed(() => real.scenarios),
   selectedScenarioId: computed(() => real.selectedScenarioId),
   intensityPercent: computed(() => real.intensityPercent),

   runId: computed(() => real.runId),
   runStatus: computed(() => real.runStatus),
   sseState: computed(() => real.sseState),
   lastError: computed(() => real.lastError),
   startRetryable: computed(() => startRetryOffered(real.lastError, real.retryableStartError)),

   runStats: computed(() => real.runStats),

   accessToken: computed(() => real.accessToken),
   adminRuns: computed(() => admin.runs.value),
   adminRunsLoading: computed(() => admin.loading.value),
   adminLastError: computed(() => admin.lastError.value),

   adminCanGetRuns: computed(() => typeof admin.getRuns === 'function'),
   adminCanStopRuns: computed(() => typeof admin.stopRuns === 'function'),
   adminCanAttachRun: computed(() => typeof admin.attachRun === 'function'),
   adminCanStopRun: computed(() => typeof admin.stopRun === 'function'),
 }

 provideTopBarContext(topBarCtx)

/** True when initial data has loaded (or we already have a snapshot from a previous load). */
const dataReady = computed(() => !state.loading || !!state.snapshot)

onMounted(() => {
  window.addEventListener('keydown', onGlobalKeydown)

  // Step 3: Viewport ResizeObserver wiring (WM-only).
  void nextTick(() => {
    const vp = readHostViewport(hostEl.value)
    wm.setViewport(vp)
    bindOverlayGeometryPublishers()
  })

  if (typeof ResizeObserver !== 'undefined') {
    viewportRo = new ResizeObserver(() => {
      const vp = readHostViewport(hostEl.value)
      wm.setViewport(vp)
      wm.reclampAll()
    })

    const host = hostEl.value
    if (host) viewportRo.observe(host)
  }
})

onUnmounted(() => {
  window.removeEventListener('keydown', onGlobalKeydown)

  viewportRo?.disconnect()
  viewportRo = null
  resetOverlayGeometryPublishers()
})

/**
 * The actions have no run to act on: none is attached (a refused or failed start, a stopped run reset, the opening's
 * start still in flight) or the run has ended. 043 S2 (review B4): until then "no run" left the three actions enabled
 * and the person got the raw "No active run_id" of the action instead of a disabled button with its reason.
 */
const interactRunTerminal = computed(() => {
  if (!real.runId) return true
  const st = toLower(real.runStatus?.state)
  return st === 'stopped' || st === 'error'
})

// F-034-16 (034 S5b): the selected line and the BASIS of its figures live in a composable, testable without a mount
// (`useSelectedTrustlineLine.test.ts`); this is where the root hands it the pair, the cache and the window state.
const selectedLine = useSelectedTrustlineLine({
  fromPid: () => interact.mode.state.fromPid,
  toPid: () => interact.mode.state.toPid,
  trustlines: () => interact.mode.trustlines.value,
  fetchState: () => interact.mode.trustlinesFetchState.value,
  findAnsweredTrustline: (from, to) => interact.mode.findAnsweredTrustline(from, to),
  snapshotLinks: () => state.snapshot?.links,
  windowState: () => wmEdgeDetail.state.value,
  frozenLink: () => wmEdgeDetail.frozenLink.value,
  frozenFiguresSource: () => wmEdgeDetail.frozenFiguresSource.value,
})
const interactSelectedLink = selectedLine.selectedLink
const interactSelectedLinkFiguresSource = selectedLine.figuresSource
const wmEdgeDetailFiguresSource = selectedLine.edgeDetailFiguresSource

type TrustlinePanelRef = InstanceType<typeof TrustlineManagementPanel>

const tlPanel = ref<TrustlinePanelRef | TrustlinePanelRef[] | null>(null)

function focusTrustlineNewLimit(): void {
  const panelRef = tlPanel.value
  if (Array.isArray(panelRef)) {
    const panel = panelRef.find((candidate) => typeof candidate?.focusNewLimit === 'function')
    panel?.focusNewLimit()
    return
  }

  if (typeof panelRef?.focusNewLimit === 'function') {
    panelRef.focusNewLimit()
  }
}

/**
 * Snapshot the selected node's screen-space center.
 * Uses the WM NodeCard's nodeId (sticky inspector), not the global selection.
 */
function snapshotNodeCenter(): Point | null {
  const win = wm.windows.value.find((w) => w.type === 'node-card' && w.state !== 'closing')
  if (!win || !isNodeCardWindow(win)) return null
  const nodeId = win.data.nodeId
  if (!nodeId) return null
  return getNodeScreenCenter(nodeId)
}

 /**
  * Returns an anchor for ActionBar-opened panels.
  *
  * Design goals:
  * - Panel appears in the top-right corner, directly under the ActionBar buttons.
  * - Stays within screen bounds regardless of viewport width.
  * - Does NOT rely on CSS `right: 12px` default — explicit inline style wins every time.
  *
  * Anchor coordinates (WM requires host-relative coordinates; see R-14):
  * - hostRect = host.getBoundingClientRect()
  * - r = barEl.getBoundingClientRect()
  * - x = r.left - hostRect.left
  * - y = r.bottom - hostRect.top
  *
  * Positioning math (see placeOverlayNearAnchor in overlayPosition.ts):
  *   left = clamp(x + offX, pad, hostRect.width - panelW - pad)
  *   top  = clamp(y + offY, pad, hostRect.height - panelH - pad)
  *
  * Fallback (legacy top-right HUD behavior):
  * - If the ActionBar element cannot be found, anchor to the host's right edge:
  *   x = hostRect.width, y = 98.
  *
  * If hostEl is unavailable (SSR / tests), returns null → CSS default applies.
  */
function getActionBarAnchor(): Point | null {
  const host = hostEl.value
  if (!host) return null

  // IMPORTANT: WM anchors must be in host-relative coordinates. The ActionBar and the top stack are measured for the
  // TOP of the window (under every row of the stack); its side is the right rail (043 S2, below).
  const hostRect = host.getBoundingClientRect()
  const actionBarContainer = host.querySelector('[aria-label="Interact actions"]') as HTMLElement | null
  if (!actionBarContainer) {
    // Fallback: previous behavior (top-right under the HUD stack).
    const y = readOverlayGeometryPx(host).hudStackHeightPx
    return { x: hostRect.width, y }
  }

  // Measure the actual HudBar (inline-flex when `fit` is enabled) to avoid accidentally
  // anchoring by a full-width wrapper.
  const barEl = (actionBarContainer.querySelector('.hud-bar') as HTMLElement | null) ?? actionBarContainer
  const r = barEl.getBoundingClientRect()

  // 043 S1: the ActionBar is in the FIRST row of the top stack; with "Подробнее" on, rows follow it. The panel opens
  // under the whole stack, never over those rows (until 043 S1 the ActionBar was the stack's last row).
  const stack = host.querySelector('.ds-ov-top-stack')
  const stackBottom = stack instanceof HTMLElement ? stack.getBoundingClientRect().bottom - hostRect.top : Number.NEGATIVE_INFINITY
  // 043 S2 (owner's decision on review item B2, 2026-10-17; mockup-s3): the window of an action opens in the RIGHT
  // RAIL under the top bar ("Подробнее" is above it), not over the centre of the graph. The anchor is the right edge
  // of the host; the window manager clamps the window inside the viewport, so it lands flush right.
  const x = hostRect.width
  const y = Math.max(r.bottom - hostRect.top, Number.isFinite(stackBottom) ? stackBottom : Number.NEGATIVE_INFINITY)
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null
  return { x, y }
}

function navigatorEdgeAnchor(link: GraphLink): Point {
  const source = getNodeScreenCenter(link.source)
  const target = getNodeScreenCenter(link.target)
  if (source && target) {
    return {
      x: (source.x + target.x) / 2,
      y: (source.y + target.y) / 2,
    }
  }
  return source ?? target ?? getActionBarAnchor() ?? { x: 24, y: 120 }
}

let interactFlowOpener: HTMLElement | null = null
let interactFlowFallback: HTMLElement | null = null
let interactFlowFocusOwnerType = 'interact-panel'
let interactFlowStrictFocusOwner: Element | null = null
let interactFlowUsesStrictFocusOwner = false

function captureInteractFlowOpener(
  fallbackSelector?: string,
  opts: { focusOwnerType?: 'interact-panel' | 'edge-detail'; preferFallback?: boolean; strictFocusOwner?: boolean } = {},
): void {
  const active = document.activeElement
  const fallback = fallbackSelector ? getHostEl()?.querySelector(fallbackSelector) : null
  interactFlowFallback = fallback instanceof HTMLElement ? fallback : null
  interactFlowOpener = opts.preferFallback ? null : active instanceof HTMLElement ? active : null
  interactFlowFocusOwnerType = opts.focusOwnerType ?? 'interact-panel'
  interactFlowUsesStrictFocusOwner = opts.strictFocusOwner === true
  const activeWindow = active instanceof Element ? active.closest('[data-win-type]') : null
  interactFlowStrictFocusOwner =
    interactFlowUsesStrictFocusOwner && activeWindow?.getAttribute('data-win-type') === interactFlowFocusOwnerType
      ? active
      : null
}

function inspectNodeFromNavigator(nodeId: string): void {
  // 043 S2 (review C1): the same selection as a double click on the canvas (`openNodeCardFromCanvasDblClick`), then the card.
  state.selectedNodeId = nodeId
  uiOpenOrUpdateNodeCard({ nodeId, anchor: getNodeScreenCenter(nodeId) })
}

/**
 * Analytics panel placement (spec 007, T705). Every value is derived from the catalog descriptor —
 * which edge, which HUD clearances, which width contract, which z layer — so the scoped rule below
 * declares the properties and the catalog decides what goes in them.
 */
const analyticsDockStyle = resolveOverlayDockStyle('real-metrics-panel')

/** The dock's own element, so its width can be measured rather than guessed from a token. */
const analyticsDockEl = ref<HTMLElement | null>(null)

/**
 * How much of the canvas's right-hand side the analytics dock is currently covering, in CSS px.
 *
 * This is the one place that knows both things at once — that a dock exists, and how wide it
 * actually is right now. The dock's width is a `min()` of a token and a percentage of the host
 * (`resolveOverlayDockStyle`), so it is measured, not read off a constant that would be wrong on
 * exactly the narrow windows where the overlap matters.
 *
 * Measured from the host's right edge to the dock's left edge, so the gap between them counts as
 * covered too — the camera must not aim into a 12px sliver. `0` whenever there is nothing to
 * measure (dock closed, or a layout-less environment such as jsdom), which is the value that
 * leaves framing exactly as it was.
 */
function analyticsDockInsetRightPx(): number {
  const host = hostEl.value
  const dock = analyticsDockEl.value
  if (!host || !dock) return 0

  const hostRect = host.getBoundingClientRect()
  const dockRect = dock.getBoundingClientRect()
  const covered = hostRect.right - dockRect.left
  if (!Number.isFinite(covered) || covered <= 0) return 0
  return Math.min(covered, hostRect.width)
}

/**
 * A bottleneck row asks to be shown on the graph.
 *
 * Edge targets frame the edge and then mark it; node targets open the node's inspector card, which
 * is the same thing the graph navigator does with a node. Both are visible responses — a row that
 * reported "focus" and then did nothing would be worse than no button at all.
 *
 * Framing alone is not an answer to "which edge": the camera fits a segment, and where trustlines
 * run in parallel the fitted region holds several of them. The active-edge highlight is the app's
 * existing way of naming one edge, so the row borrows it rather than inventing a second marker.
 *
 * The highlight goes on strictly *after* `focusOnEdge` confirms the edge is still in the snapshot.
 * A `false` there means the graph moved on between the panel's poll and this click; painting a
 * highlight anyway would point at whatever edge now owns that key — a second wrong answer layered
 * on a camera that correctly refused to move.
 *
 * TTL: 3000ms. The overlay fades over its last 1200ms, so this is ~1.8s at full strength and then
 * a visible decay — long enough for a gaze to travel from the panel row to the canvas after the
 * camera jumps, short enough that it is unambiguously gone before the next row is clicked. The
 * automatic effects sit either side of it on purpose: 1500ms for a single scripted transfer pulse,
 * 5200ms for a clearing cascade that lights many edges at once and has to outlast its own animation.
 */
function focusBottleneckTarget(target: BottleneckTarget): void {
  if (target.kind === 'edge') {
    // The click came from the dock, so the dock is open and covering the right-hand side of the
    // canvas. Framing into the middle of the full canvas would put the edge under the very panel
    // the row was clicked in — on a narrow window, sometimes entirely under it.
    const insetRight = analyticsDockInsetRightPx()
    const focused =
      insetRight > 0
        ? focusOnEdge(target.from, target.to, { viewportInsetRight: insetRight })
        : focusOnEdge(target.from, target.to)
    if (!focused) return
    addActiveEdge(keyEdge(target.from, target.to), 3000)
    return
  }
  inspectNodeFromNavigator(target.id)
}

function inspectEdgeFromNavigator(link: GraphLink): void {
  if (apiMode.value !== 'real' || !isLiveUi.value || interact.mode.busy.value) return

  const anchor = navigatorEdgeAnchor(link)
  if (!interact.mode.selectEdge(keyEdge(link.source, link.target), anchor)) return
  uiOpenOrUpdateEdgeDetail({ fromPid: link.source, toPid: link.target, anchor })
}

function cancelInteractWindowFromUi(): void {
  const interactWindow = wm.windows.value.find((win) => win.type === 'interact-panel' && win.state !== 'closing')
  if (interactWindow) {
    // The WM interact-panel policy delegates action-close to the current FSM onClose owner.
    wm.close(interactWindow.id, 'action')
    const opener = interactFlowOpener
    const fallback = interactFlowFallback
    interactFlowOpener = null
    interactFlowFallback = null
    interactFlowFocusOwnerType = 'interact-panel'
    interactFlowStrictFocusOwner = null
    interactFlowUsesStrictFocusOwner = false
    void nextTick(() => {
      const target = opener?.isConnected && !opener.matches(':disabled') ? opener : fallback
      if (document.activeElement !== document.body || !target?.isConnected || target.matches(':disabled')) return
      target.focus({ preventScroll: true })
    })
    return
  }
  interactFlowOpener = null
  interactFlowFallback = null
  interactFlowFocusOwnerType = 'interact-panel'
  interactFlowStrictFocusOwner = null
  interactFlowUsesStrictFocusOwner = false
  interact.mode.cancel()
}

/**
 * 043 S2: the source of the scene - the server (a live run) or the offline snapshot - is the "Источник" select under
 * "Подробнее" (decision 2). Switching it replaces the whole data pipeline, so it reloads the page with `?mode=`; it is
 * the one switch of this screen that still navigates (the Sandbox / Auto-Run / Interact segment and its three reloads
 * are gone). Entering a manual action never navigates (reproducer (d)).
 */
function setSource(next: 'real' | 'fixtures') {
  if (next === apiMode.value) return
  if (toLower(interactPhase.value) !== 'idle') interact.mode.cancel()
  const u = new URL(window.location.href)
  u.searchParams.set('mode', next)
  // An old `?ui=` (`interact`, `demo` - both ignored since 043 S2/S3) and `?debug=` are not carried across the switch.
  u.searchParams.delete('ui')
  u.searchParams.delete('debug')
  // Setting href ensures a full re-init: the data pipeline is replaced (043 S3: the one navigation left on this screen).
  window.location.href = u.toString()
}

// Interact Mode state is provided by useSimulatorApp() (core-only; panels/picking wiring is a later task).

 function onEdgeDetailChangeLimit() {
  captureInteractFlowOpener('[data-testid="actionbar-trustline"]')
  // MUST: anchor propagation from edge popup to interact-panel.
  wmEdgePopupAnchor.value = interact.mode.state.edgeAnchor ?? null

   // Audit fix L-4 / Step 5: close ONLY the edge-detail inspector window, then open trustline.
   wmEdgeDetail.close({ suppress: false })
   wmEdgeDetail.applyToWindowManager(wm, { closeReason: 'programmatic' })

  // Switch from EdgeDetailPopup (quick info) to TrustlineManagementPanel (full editor).
  useFullTrustlineEditor.value = true
  // 043 S2: opening the full editor is a manual action of its own (no flow transition happens): it pauses the run.
  void realActions.pauseForManualAction()
  // Focus the limit editor in TrustlineManagementPanel via template ref.
  // Defensive: template refs rendered through TransitionGroup / repeated windows may resolve
  // either to a single instance or to an array of instances.
  void nextTick(() => {
    focusTrustlineNewLimit()
  })
}

function onEdgeDetailCloseLine() {
  // Delegate to mode action (will transition to idle on success).
  if (interactPhase.value !== 'editing-trustline') return
  // `F-013-7`: отключённая кнопка — это видимость гарда, а не гард: этот обработчик
  // вызывается по событию от попапа, и решение о мутации принимается здесь же, где известно основание.
  if (!canActOnTrustlineFigures(wmEdgeDetailFiguresSource.value)) return
  // 043 S2 (review C2): closing a line from its popup is a manual act of its own.
  void realActions.pauseForManualAction()
  captureInteractFlowOpener('[data-testid="actionbar-trustline"]', {
    focusOwnerType: 'edge-detail',
    preferFallback: true,
    strictFocusOwner: true,
  })
  void interact.mode.confirmTrustlineClose()
}

 function onEdgeDetailSendPayment() {
  // trustline from→to => payment to→from
  const fromPid = interact.mode.state.fromPid
  const toPid = interact.mode.state.toPid
  if (!fromPid || !toPid) return
  captureInteractFlowOpener('[data-testid="actionbar-payment"]')

  // MUST: if initiated from edge popup, propagate edge anchor to interact-panel.
  wmEdgePopupAnchor.value = interact.mode.state.edgeAnchor ?? null

   // KeepAlive: edge-detail stays open as context for the payment flow.
   // Spec: "инспектор остаётся открыт как «база контекста», а Interact-панель
   // открывается поверх для выполнения действия (Send Payment)."
   // Freeze the current link data before cancel() clears interact state.
   // Основание замораживается ВМЕСТЕ с числами: снимок молчания обязан остаться снимком
   // молчания (внешнее ревью 013, находка P3).
   wmEdgeDetail.allowKeepAlive({
     frozenLink: interactSelectedLink.value,
     frozenFiguresSource: interactSelectedLinkFiguresSource.value,
     // 043 S1 (review, class 1): the unit of the frozen figures is frozen with them.
     frozenEquivalent: sceneEq.value,
   })
   // Do NOT close edge-detail — it persists as context.

  // Atomically start payment with pre-filled FROM to avoid an intermediate
  // picking-payment-from phase (which can flash a useless empty window in WM mode).
  interact.mode.cancel()
  interact.mode.startPaymentFlowWithFrom(toPid)
  interact.mode.setPaymentToPid(fromPid)
}

function startFlowFromNodeCard(opts: {
  openEditor?: boolean
  start: () => void
}) {
  captureInteractFlowOpener()
  // Not an edge-popup initiated action.
  wmEdgePopupAnchor.value = null
  // Snapshot NodeCard node screen center BEFORE phase changes.
  const snapshot = snapshotNodeCenter()
  rightRailAnchorActive = false

  if (opts.openEditor) useFullTrustlineEditor.value = true
  // H-1 coexistence: NodeCard is an inspector window and must coexist with interact.
  // WM: set anchor BEFORE phase change so the first `wm.open()` uses it.
  wmPanelOpenAnchor.value = snapshot
  opts.start()
}

function startFlowFromActionBar(opts: { openEditor?: boolean; start: () => void }) {
  captureInteractFlowOpener()
  // Not an edge-popup initiated action.
  wmEdgePopupAnchor.value = null
  // Snapshot ActionBar anchor BEFORE opts.start() changes the phase.
  const snapshot = getActionBarAnchor()
  rightRailAnchorActive = true
  if (opts.openEditor) useFullTrustlineEditor.value = true

  // WM: set anchor BEFORE phase change so the first `wm.open()` uses it.
  wmPanelOpenAnchor.value = snapshot
  opts.start()
}

// BUG-1: NodeCardOverlay interact mode handlers
function onInteractSendPayment(fromPid: string) {
  startFlowFromNodeCard({
    start: () => {
      interact.mode.startPaymentFlowWithFrom(fromPid)
    },
  })
}

function onInteractNewTrustline(fromPid: string) {
  startFlowFromNodeCard({
    // NodeCard-initiated trustline flows have no edge anchor for the quick inspector.
    // Keep using the full editor when the flow advances to create/edit phases.
    openEditor: true,
    start: () => {
      interact.mode.startTrustlineFlowWithFrom(fromPid)
    },
  })
}

function onInteractEditTrustline(fromPid: string, toPid: string) {
  startFlowFromNodeCard({
    openEditor: true,
    start: () => {
      interact.mode.selectTrustline(fromPid, toPid)
    },
  })
}

function onActionStartPaymentFlow() {
  startFlowFromActionBar({
    start: () => {
      interact.mode.startPaymentFlow()
    },
  })
}

function onActionStartTrustlineFlow() {
  startFlowFromActionBar({
    openEditor: true,
    start: () => {
      interact.mode.startTrustlineFlow()
    },
  })
}

function onActionStartClearingFlow() {
  startFlowFromActionBar({
    start: () => {
      interact.mode.startClearingFlow()
    },
  })
}

/**
 * 043 S1: the one onboarding hint (decision 1; a static line until the tour of 034 S9). Shown while a scene is on
 * screen until the first action: its own close button, "Подробнее", a participant selected, or any manual flow
 * started. 043 S2: remembered in this browser (`readOnboardingDismissed`) - once gone, it does not come back on the next
 * load. Not in the offline screenshot mode (isE2eScreenshots), which has its own minimal controls.
 */
const onboardingDismissed = ref(simulatorStorage.readOnboardingDismissed() ?? false)
const showOnboardingHint = computed(() => !onboardingDismissed.value && !isE2eScreenshots.value && !!state.snapshot)
const onboardingCanAct = computed(() => isLiveUi.value)
function dismissOnboarding(): void {
  if (onboardingDismissed.value) return
  onboardingDismissed.value = true
  simulatorStorage.writeOnboardingDismissed(true)
}
watch(detailsOpen, dismissOnboarding)
watch(() => state.selectedNodeId, (id) => { if (id) dismissOnboarding() })
watch(interactPhase, (p) => { if (toLower(p) !== 'idle') dismissOnboarding() })

// When phase changes, reset local UI flags.
// Reset useFullTrustlineEditor when leaving trustline phases (idle, payment, clearing)
// so the NEXT edge click shows EdgeDetailPopup (not full editor).
// NOTE: do NOT reset when transitioning between trustline sub-phases
// (e.g. picking-trustline-to → editing-trustline) to preserve the ActionBar intent.
watch([interactPhase, interact.mode.busy], ([phase, busy]) => {
  const p = toLower(phase)
  if (!p.includes('trustline')) {
    useFullTrustlineEditor.value = false
  }

  if (p === 'idle' && !busy && (interactFlowOpener || interactFlowFallback)) {
    const opener = interactFlowOpener
    const fallback = interactFlowFallback
    const focusOwnerType = interactFlowFocusOwnerType
    const strictFocusOwner = interactFlowStrictFocusOwner
    const usesStrictFocusOwner = interactFlowUsesStrictFocusOwner
    interactFlowOpener = null
    interactFlowFallback = null
    interactFlowFocusOwnerType = 'interact-panel'
    interactFlowStrictFocusOwner = null
    interactFlowUsesStrictFocusOwner = false
    void nextTick(() => {
      const target = opener?.isConnected && !opener.matches(':disabled') ? opener : fallback
      const active = document.activeElement
      const activeWindow = active instanceof Element ? active.closest('[data-win-type]') : null
      const focusStillOwnedByFlow = usesStrictFocusOwner
        ? Boolean(strictFocusOwner && (active === strictFocusOwner || (active instanceof Node && strictFocusOwner.contains(active))))
        : activeWindow?.getAttribute('data-win-type') === focusOwnerType
      if (
        (active !== document.body && !focusStillOwnedByFlow) ||
        !target?.isConnected ||
        target.matches(':disabled')
      ) return
      target.focus({ preventScroll: true })
    })
  }
})
</script>

<template>
  <div
    ref="hostEl"
    class="root ds-ov-vars"
    :data-theme="uiTheme"
    data-density="comfortable"
    :data-motion="reducedMotion ? 'reduced' : 'full'"
    :aria-busy="state.loading || real.loadingScenarios || interact.mode.busy.value ? 'true' : 'false'"
    :data-ready="!state.loading && !state.error && state.snapshot ? '1' : '0'"
    :data-scene="scene"
    :data-layout="layoutMode"
    :data-quality="quality"
    :data-gpu="gpuAccelLikely ? '1' : '0'"
    :data-webdriver="isWebDriver ? '1' : '0'"
    :style="{ '--overlay-scale': String(overlayLabelScale) }"
  >
    <canvas
      ref="canvasEl"
      class="canvas"
      aria-hidden="true"
      :style="{ cursor: isInteractPickingPhase ? 'crosshair' : 'default' }"
      @click="onCanvasClick"
      @dblclick.prevent="onCanvasDblClick"
      @pointerdown="onCanvasPointerDown"
      @pointermove="onCanvasPointerMove"
      @pointerup="onCanvasPointerUp"
      @pointercancel="onCanvasPointerUp"
      @pointerleave="clearHoveredEdge"
      @wheel.prevent="onCanvasWheel"
    />
    <canvas ref="fxCanvasEl" class="canvas canvas-fx" aria-hidden="true" />

    <div ref="dragPreviewEl" class="drag-preview" aria-hidden="true" />

    <EdgeTooltip
      :edge="hoveredEdge"
      :style="edgeTooltipStyle"
      :get-node-name="(id) => getNodeById(id)?.name ?? null"
      :interact-mode="isLiveUi"
    />

    <TopBar
      :details-open="detailsOpen"
      @toggle-details="toggleDetails"
      @set-ui-theme="setUiTheme"
      @refresh-scenarios="realActions.refreshScenarios"
      @start-run="realActions.startRun"
      @pause="realActions.pause"
      @resume="realActions.resume"
      @stop="realActions.stop"
      @apply-intensity="realActions.applyIntensity"
      @admin-get-runs="admin.getRuns"
      @admin-stop-runs="admin.stopRuns"
      @admin-attach-run="admin.attachRun"
      @admin-stop-run="admin.stopRun"
      @update:selected-scenario-id="realActions.setSelectedScenarioId"
      @update:intensity-percent="realActions.setIntensityPercent"
    >
      <!-- 043 S2: the three protocol actions in the first row of the one screen (server source). -->
      <template #actions>
        <ActionBar
          v-if="dataReady && isLiveUi"
          :phase="interactPhase"
          :busy="interact.mode.busy.value"
          :cancelling="interact.mode.cancelling?.value ?? false"
          :actions-disabled="interact.actions.actionsDisabled.value"
          :run-terminal="interactRunTerminal"
          :start-payment-flow="onActionStartPaymentFlow"
          :start-trustline-flow="onActionStartTrustlineFlow"
          :start-clearing-flow="onActionStartClearingFlow"
        />
      </template>

      <!-- 043 S1: the full metrics, below the rows, while Details is on (the total debt is in the bottom bar).
           No snapshot - no balance (034 S8, review C3): unknown debts are not zero debts. -->
      <SystemBalanceBar
        v-if="detailsOpen && dataReady && isLiveUi && state.snapshot"
        :balance="interact.systemBalance"
        :equivalent="sceneEq"
        variant="metrics"
      />

      <template #hint>
        <div v-if="showOnboardingHint" class="ds-panel sar-onboarding-hint" data-testid="onboarding-hint">
          <span class="sar-onboarding-hint__icon" aria-hidden="true">💡</span>
          <span class="sar-onboarding-hint__text">
            Линии — доверие между участниками. Выберите участника и наведите на его линию: кто кому доверяет и кто кому должен.
            <template v-if="onboardingCanAct"> Нажмите <b>Send Payment</b>, чтобы попробовать самому.</template>
          </span>
          <button type="button" class="ds-btn ds-btn--ghost ds-btn--sm sar-onboarding-hint__close" aria-label="Close hint" @click="dismissOnboarding">✕</button>
        </div>
      </template>
    </TopBar>

    <!-- Step 2: WindowLayer (renders migrated windows from WM state). -->
    <!-- R21: unified open/close transition for all windows. -->
    <!-- P1-3: @after-leave calls finishClose() to complete removal from WM map after animation. -->
    <TransitionGroup
      name="ws"
      @after-leave="(el) => { const id = parseInt((el as HTMLElement).dataset?.winId ?? ''); if (id) wm.finishClose(id) }"
      tag="div"
      class="wm-layer"
      data-density="compact"
      aria-label="Window layer"
      :css="!isTestMode"
    >
      <!-- Frameless: WindowShell renders no close button; each window closes from its own controls (×, Cancel, Close). -->
        <WindowShell
        v-for="win in wm.windows.value"
        :key="win.id"
        :instance="win"
        :role="wmSurfaceDescriptorFor(win).role"
        :aria-label="wmSurfaceDescriptorFor(win).ariaLabel"
        :title="wmSurfaceDescriptorFor(win).title"
        :frameless="true"
        @focus="wm.focus(win.id)"
        @measured="(s) => {
          wm.updateMeasuredSize(win.id, s)
          wm.reclamp(win.id)
        }"
      >
        <EdgeDetailPopup
          v-if="win.type === 'edge-detail'"
          :phase="wmEdgeDetailEffectivePhase"
          :state="wmEdgeDetailEffectiveState(win)"
          :unit="wmEdgeDetailEffectiveUnit"
          :used="emptyToNull(wmEdgeDetailEffectiveLink?.used)"
          :reverse-used="emptyToNull(wmEdgeDetailEffectiveLink?.reverse_used)"
          :limit="emptyToNull(wmEdgeDetailEffectiveLink?.trust_limit)"
          :available="emptyToNull(wmEdgeDetailEffectiveLink?.available)"
          :status="emptyToNullString(wmEdgeDetailEffectiveLink?.status)"
          :close-requested-at="wmEdgeDetailEffectiveLink?.close_requested_at ?? null"
          :busy="wmEdgeDetailEffectiveBusy"
          :figures-source="wmEdgeDetailFiguresSource"
          :close="() => uiCloseEdgeDetailWindow('action')"
          @change-limit="onEdgeDetailChangeLimit"
          @close-line="onEdgeDetailCloseLine"
          @send-payment="onEdgeDetailSendPayment"
        />

        <NodeCardOverlay
          v-else-if="isNodeCardWindow(win) && getNodeById(String(win.data.nodeId))"
          :node="getNodeById(String(win.data.nodeId))!"
          :edge-stats="nodeEdgeStatsFor(String(win.data.nodeId))"
          :equivalent-text="state.snapshot?.equivalent ?? ''"
          :palette="dataPalette"
          :show-pin-actions="!isTestMode && !isWebDriver"
          :is-pinned="isNodePinned(String(win.data.nodeId))"
          :pin="() => pinNode(String(win.data.nodeId))"
          :unpin="() => unpinNode(String(win.data.nodeId))"
          :interact-mode="isLiveUi"
          :interact-trustlines="isLiveUi ? interact.mode.trustlines.value : undefined"
          :trustlines-loading="isLiveUi ? interact.mode.trustlinesLoading.value : undefined"
          :interact-busy="isLiveUi ? interact.mode.busy.value : undefined"
          :interact-phase="isLiveUi ? interactPhase : undefined"
          :on-interact-send-payment="isLiveUi ? onInteractSendPayment : undefined"
          :on-interact-new-trustline="isLiveUi ? onInteractNewTrustline : undefined"
          :on-interact-edit-trustline="isLiveUi ? onInteractEditTrustline : undefined"
          @close="() => {
            wm.close(win.id, 'action')
          }"
        />

        <ManualPaymentPanel
          v-else-if="isWmInteractPanelWindow(win, 'payment')"
          :phase="wmInteractPanelPhase(win)"
          :state="interact.mode.state"
          :unit="sceneEq"
          :available-capacity="interact.mode.availableCapacity.value"
          :trustlines-loading="trustlinesLoading"
          :payment-targets-loading="paymentTargetsLoading"
          :payment-targets-max-hops="paymentTargetsMaxHops"
          :payment-to-target-ids="paymentToTargetIds"
          :payment-to-last-known-target-ids="interact.mode.paymentToLastKnownTargetIds?.value"
          :trustlines="trustlines"
          :trustlines-last-error="trustlinesLastError"
          :payment-targets-last-error="paymentTargetsLastError"
          :participants="interact.mode.participants.value"
          :busy="interact.mode.busy.value"
          :can-send-payment="interact.mode.canSendPayment.value"
          :confirm-payment="interact.mode.confirmPayment"
          :set-from-pid="interact.mode.setPaymentFromPid"
          :set-to-pid="interact.mode.setPaymentToPid"
          :cancel="cancelInteractWindowFromUi"
          :trustlines-state="interact.mode.trustlinesFetchState.value.kind"
          :payment-outcome="interact.mode.paymentOutcome.value"
          :retry-payment="interact.mode.retryPayment"
          :dismiss-payment-result="interact.mode.dismissPaymentResult"
          :discard-unresolved-payment="interact.mode.discardUnresolvedPayment"
          :target-estimate="interact.mode.paymentTargetEstimate.value"
        />

        <TrustlineManagementPanel
          v-else-if="isWmInteractPanelWindow(win, 'trustline')"
          ref="tlPanel"
          :phase="wmInteractPanelPhase(win)"
          :state="interact.mode.state"
          :unit="sceneEq"
          :used="emptyToNull(interactSelectedLink?.used)"
          :current-limit="emptyToNull(interactSelectedLink?.trust_limit)"
          :available="emptyToNull(interactSelectedLink?.available)"
          :figures-source="interactSelectedLinkFiguresSource"
          :participants="interact.mode.participants.value"
          :trustlines="interact.mode.trustlines.value"
          :busy="interact.mode.busy.value"
          :confirm-trustline-create="interact.mode.confirmTrustlineCreate"
          :confirm-trustline-update="interact.mode.confirmTrustlineUpdate"
          :confirm-trustline-close="interact.mode.confirmTrustlineClose"
          :set-from-pid="interact.mode.setTrustlineFromPid"
          :set-to-pid="interact.mode.setTrustlineToPid"
          :select-trustline="interact.mode.selectTrustline"
          :cancel="cancelInteractWindowFromUi"
        />

        <ClearingPanel
          v-else-if="isWmInteractPanelWindow(win, 'clearing')"
          :phase="wmInteractPanelPhase(win)"
          :state="interact.mode.state"
          :busy="interact.mode.busy.value"
          :equivalent="sceneEq"
          :participants="interact.mode.participants.value"
          :confirm-clearing="interact.mode.confirmClearing"
          :cancel="cancelInteractWindowFromUi"
        />
      </WindowShell>
    </TransitionGroup>
    <!-- E2E screenshot tests: minimal offline controls only (match stored snapshots). -->
    <div v-if="isE2eScreenshots" class="ds-ov-bottom ds-panel ds-ov-bar">
      <!-- 039 slice B: the legend is not hidden for the stored snapshots - it is part of the picture. -->
      <div class="ds-ov-bottom__legend sar-e2e-legend">
        <Legend :nodes="state.snapshot?.nodes" :equivalent="shownSnapshotEquivalent" :palette="dataPalette" />
      </div>
      <button class="ds-btn ds-btn--secondary" type="button" @click="e2e.runTxOnce">Single Tx</button>
      <button class="ds-btn ds-btn--secondary" type="button" @click="e2e.runClearingOnce">Run Clearing</button>
    </div>

    <BottomBar
      v-else
      v-model:eq="eq"
      v-model:layoutMode="layoutMode"
      v-model:scene="scene"
      v-model:quality="quality"
      v-model:labelsLod="labelsLod"
      :api-mode="apiMode"
      :set-source="setSource"
      :playlists="playlistWorkbench"
      :is-demo-fixtures="isDemoFixtures"
      :equivalent-options="equivalentOptions"
      :show-reset-view="showResetView"
      :reset-view="resetView"
      :run-id="real.runId"
      :refresh-snapshot="realActions.refreshSnapshot"
      :artifacts="real.artifacts"
      :artifacts-loading="real.artifactsLoading"
      :refresh-artifacts="realActions.refreshArtifacts"
      :download-artifact="realActions.downloadArtifact"
      :analytics-panel-open="analyticsPanelOpen"
      :toggle-analytics-panel="toggleAnalyticsPanel"
      :details-open="detailsOpen"
    >
      <!-- 039 slice B: a row INSIDE the bottom bar, above its controls; the bar's measured height includes it. -->
      <template #legend>
        <Legend :nodes="state.snapshot?.nodes" :equivalent="shownSnapshotEquivalent" :palette="dataPalette" />
      </template>

      <!-- 043 S1: the compact total debt, always in the bottom row. -->
      <template v-if="dataReady && isLiveUi && state.snapshot" #summary>
        <SystemBalanceBar :balance="interact.systemBalance" :equivalent="sceneEq" variant="debt" />
      </template>

      <!-- 043 S1: "Inspect graph" (the keyboard path to nodes and lines) is a button of the bottom row, behind Details. -->
      <template v-if="dataReady && !isE2eScreenshots" #navigator>
        <GraphNavigator
          :nodes="state.snapshot?.nodes ?? []"
          :links="state.snapshot?.links ?? []"
          :edge-inspect-disabled="apiMode !== 'real' || !isLiveUi || interact.mode.busy.value"
          @inspect-node="inspectNodeFromNavigator"
          @inspect-edge="inspectEdgeFromNavigator"
        />
      </template>
    </BottomBar>

    <!-- Analytics overlay surface (spec 007, T705). Registered in `overlaySurfaceCatalog`; the
         geometry below is the descriptor's, not this component's. -->
    <div
      v-if="analytics.isVisible.value"
      ref="analyticsDockEl"
      class="sar-analytics-dock"
      data-surface="real-metrics-panel"
      :style="analyticsDockStyle"
    >
      <RealMetricsPanel
        :metrics-phase="analytics.metricsPhase.value"
        :bottlenecks-phase="analytics.bottlenecksPhase.value"
        :metrics="analytics.metrics.value"
        :bottlenecks="analytics.bottlenecks.value"
        :metrics-error="analytics.metricsError.value"
        :bottlenecks-error="analytics.bottlenecksError.value"
        :metrics-unavailable-reason="analytics.metricsUnavailableReason.value"
        :bottlenecks-unavailable-reason="analytics.bottlenecksUnavailableReason.value"
        :get-node-name="(id) => getNodeById(id)?.name ?? null"
        @focus-bottleneck="focusBottleneckTarget"
      />
    </div>

    <!-- Loading / error overlay (fail-fast, but non-intrusive).
         Hide the overlay during incremental updates (when we already have a snapshot)
         to avoid a visible "Loading…" flash on preview → run transitions. -->
    <div
      v-if="state.loading && !state.snapshot"
      class="ds-ov-inset"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <div class="ds-ov-message">
        <div class="ds-ov-message__title">Loading…</div>
      </div>
    </div>
    <div v-else-if="state.error" class="ds-ov-inset" role="alert" aria-atomic="true">
      <div class="ds-ov-message">
        <div class="ds-ov-message__title">Error</div>
        <div class="ds-ov-message__text">{{ state.error }}</div>
      </div>
    </div>

    <!-- No scene, no event labels (034 S8 -> 038 S1, BACKLOG 034-6 (a)): after a failed load of another source the
         snapshot is null, and an amount label of an event that still arrives would be drawn at a position of the
         old layout. Node labels are already empty without a snapshot (`useLabelNodes`). -->
    <LabelsOverlayLayers
      v-if="state.snapshot"
      :label-nodes="labelNodes"
      :floating-labels="floatingLabelsViewFx"
      :world-to-css-translate-no-scale="worldToCssTranslateNoScale"
    />

    <DevPerfOverlay :enabled="showPerfOverlay" :perf="perf" />

    <!-- BUG-6: Error toast for Interact Mode (auto-dismiss 4s). -->
    <SuccessToast
      v-if="isLiveUi"
      :message="interact.mode.successMessage"
      @dismiss="interact.mode.successMessage.value = null"
    />

    <ErrorToast
      v-if="isLiveUi"
      :message="interact.mode.state.error"
      @dismiss="interact.mode.state.error = null"
    />

    <!-- BUG-5: Interact Mode inline history log. -->
    <div
      v-if="isLiveUi && interact.mode.history.length > 0"
      class="ds-ov-bottom sar-interact-history-overlay"
    >
      <InteractHistoryLog :entries="interact.mode.history" :max-visible="3" />
    </div>
  </div>
</template>

<style scoped>
/*
  The WindowManager layer. Its z-index is the whole stacking context every window lives in:
  `effectiveZ` orders windows WITHIN it and can never lift one above it, so any surface that shares
  this value and comes later in the markup sits above every window at once. Surfaces meant to stay
  behind windows therefore take a lower layer of their own (`--ds-z-analytics-dock`), instead of
  the relation being decided by sibling order down in the template.
*/
.wm-layer {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: var(--ds-z-panel);
}

.wm-layer :deep(.ws-shell) {
  pointer-events: auto;
}

/*
  Analytics dock (spec 007, T705). Declarations only — every value comes from
  `resolveOverlayDockStyle('real-metrics-panel')`, so moving the surface means editing the catalog
  descriptor, not this rule.
*/
.sar-analytics-dock {
  position: absolute;
  top: var(--ds-ov-dock-top);
  bottom: var(--ds-ov-dock-bottom);
  left: var(--ds-ov-dock-left);
  right: var(--ds-ov-dock-right);
  width: var(--ds-ov-dock-width);
  z-index: var(--ds-ov-dock-z);

  display: flex;
  flex-direction: column;
  min-height: 0;
  pointer-events: auto;
}

.sar-e2e-legend {
  flex: 1 0 100%;
  margin-bottom: 0;
}

/* 043 S1: the onboarding hint hangs under the top stack (absolute: not part of its measured height), at the right edge,
   under "Подробнее": the scene is framed into the middle of the screen, and a hint on the left covered its first nodes
   (seen on the stand: a node of the ring and a payment's beam under it). */
.sar-onboarding-hint {
  position: absolute;
  top: calc(100% + var(--ds-space-2, 8px));
  right: 0;
  max-width: min(360px, 100%);
  font-size: 0.86rem;
  display: flex;
  align-items: flex-start;
  gap: var(--ds-space-2, 8px);
  padding: var(--ds-space-2, 8px) var(--ds-space-3, 12px);
  pointer-events: auto;
  line-height: 1.4;
}

.sar-onboarding-hint__text {
  flex: 1 1 auto;
}

.sar-onboarding-hint__close {
  flex: 0 0 auto;
}

.sar-interact-history-overlay {
  right: var(--ds-sar-interact-history-right);
  left: auto;
  bottom: var(--ds-sar-interact-history-bottom);
  padding: var(--ds-sar-interact-history-pad-y) var(--ds-sar-interact-history-pad-x);
  pointer-events: none;
}
</style>
