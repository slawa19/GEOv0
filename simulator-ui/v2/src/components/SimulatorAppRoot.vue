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

import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

 import { provideTopBarContext, type TopBarContext } from '../composables/useTopBarContext'

 import type { InteractPhase } from '../composables/useInteractMode'
import { provideActivePanelState } from '../composables/useActivePanelState'
 import type { Point } from '../types/layout'
 import { useSimulatorStorage } from '../composables/usePersistedSimulatorPrefs'
 import { normalizeUiThemeId, type UiThemeId } from '../types/uiPrefs'
 import { toLower } from '../utils/stringHelpers'
 import { extractErrorMessage } from '../utils/errorMessage'

// TD-1: all localStorage access is delegated to this composable.
const simulatorStorage = useSimulatorStorage()

function readThemeFromUrl(): UiThemeId {
  try {
    return normalizeUiThemeId(new URLSearchParams(window.location.search).get('theme'))
  } catch {
    return 'hud'
  }
}

function readThemeFromStorage(): UiThemeId | null {
  // TD-1: delegated to composable — no direct localStorage access.
  return simulatorStorage.readUiTheme()
}

function pickInitialTheme(): UiThemeId {
  const url = readThemeFromUrl()
  if (url !== 'hud') return url
  const stored = readThemeFromStorage()
  return stored ?? 'hud'
}

const uiTheme = ref<UiThemeId>(pickInitialTheme())

function syncThemeFromUrl() {
  const t = readThemeFromUrl()
  uiTheme.value = t
}

function setUiTheme(next: UiThemeId) {
  const theme = normalizeUiThemeId(next)
  uiTheme.value = theme
  // TD-1: delegated to composable — no direct localStorage access.
  simulatorStorage.writeUiTheme(theme)

  try {
    const u = new URL(window.location.href)
    if (theme === 'hud') u.searchParams.delete('theme')
    else u.searchParams.set('theme', theme)
    window.history.replaceState({}, '', u.toString())
  } catch {
    // ignore
  }
}

onMounted(() => {
  // Allow browser back/forward to update theme without reload.
  window.addEventListener('popstate', syncThemeFromUrl)

  // URL cleanup (legacy): remove dead param `devtools=1` (do not reload).
  // This param is not a source of truth; state is persisted in localStorage.
  try {
    const u = new URL(window.location.href)
    if (u.searchParams.has('devtools')) {
      u.searchParams.delete('devtools')
      window.history.replaceState({}, '', u.toString())
    }
  } catch {
    // ignore
  }

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

import { useSimulatorApp } from '../composables/useSimulatorApp'
import { computeNodeEdgeStats } from '../composables/useSelectedNodeEdgeStats'
 import { emptyToNull, emptyToNullString } from '../utils/valueFormat'
 import { keyEdge } from '../utils/edgeKey'
 import { useWindowManager } from '../composables/windowManager/useWindowManager'
 import type { WindowInstance } from '../composables/windowManager/types'
 import type { WindowAnchor } from '../composables/windowManager/types'
 import { isInteractPanelWindow, isNodeCardWindow, isEdgeDetailWindow } from '../composables/windowManager/types'
 import { interactWindowOfPhase } from '../composables/windowManager/interactWindowOfPhase'
 import { useWmEdgeDetail } from '../composables/useWmEdgeDetail'
 import { useWindowController } from '../composables/useWindowController'
 import { readOverlayGeometryPx } from '../ui-kit/overlayGeometry'
import { DEFAULT_VIEWPORT_FALLBACK_HEIGHT_PX, DEFAULT_VIEWPORT_FALLBACK_WIDTH_PX } from '../ui-kit/overlayGeometry'

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
  if (windowController) {
    windowController.uiOpenOrUpdateNodeCard(o)
    return
  }
  queueMicrotask(() => windowController?.uiOpenOrUpdateNodeCard(o))
}

const app = useSimulatorApp({
  uiCloseTopmostInspectorWindow,
  uiOpenOrUpdateEdgeDetail,
  uiOpenOrUpdateNodeCard,
  uiIsNodeCardOpen: () => wm.windows.value.some((w) => w.type === 'node-card'),
})

// MVP safety: ensure viewport isn't 0×0 even before `.root` ref is available.
wm.setViewport(readWindowViewportFallback())

let viewportRo: ResizeObserver | null = null

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

const {
  apiMode,

  // flags
  isDemoFixtures,
  isDemoUi,
  isInteractUi,
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

  effectiveEq,

  interact,

  // derived interact UI helpers
  isInteractPickingPhase,

  // env
  gpuAccelLikely,

  // refs
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
  fxDebug,

  e2e,

  // selection + overlays
  hoveredEdge,
  clearHoveredEdge,
  edgeTooltipStyle: calcEdgeTooltipStyle,
  selectedNode,
  getNodeScreenCenter,

  // pinning
  dragToPin,
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

  // helpers for template
  getNodeById,
  resetView,
} = app

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

const { activePanelType } = provideActivePanelState(interactPhase)

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
  isInteractUi,
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

function wmTitleFor(win: WindowInstance): string {
  if (isInteractPanelWindow(win)) {
    if (win.data.panel === 'payment') return 'Manual payment'
    if (win.data.panel === 'trustline') return 'Trustline'
    if (win.data.panel === 'clearing') return 'Clearing'
  }
  return ''
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

const isInteractActivePhase = computed(() => {
  if (!isInteractUi.value) return false
  return toLower(interactPhase.value) !== 'idle'
})

const activeSegment = computed(() =>
  apiMode.value !== 'real' ? 'sandbox' : isInteractUi.value ? 'interact' : 'auto'
)

 const topBarCtx: TopBarContext = {
   apiMode,
   activeSegment,
   isInteractUi,
   isTestMode,

   uiTheme,

   loadingScenarios: computed(() => real.loadingScenarios),
   scenarios: computed(() => real.scenarios),
   selectedScenarioId: computed(() => real.selectedScenarioId),
   desiredMode: computed(() => real.desiredMode),
   intensityPercent: computed(() => real.intensityPercent),

   runId: computed(() => real.runId),
   runStatus: computed(() => real.runStatus),
   sseState: computed(() => real.sseState),
   lastError: computed(() => real.lastError),

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

/** True when running in Auto-Run mode (real API, non-interact, non-demo). */
const isAutoRunUi = computed(() => apiMode.value === 'real' && !isInteractUi.value && !isDemoUi.value)

/** True when initial data has loaded (or we already have a snapshot from a previous load). */
const dataReady = computed(() => !state.loading || !!state.snapshot)

onMounted(() => {
  window.addEventListener('keydown', onGlobalKeydown)

  // Step 3: Viewport ResizeObserver wiring (WM-only).
  void nextTick(() => {
    const vp = readHostViewport(hostEl.value)
    wm.setViewport(vp)
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
})

const interactRunTerminal = computed(() => {
  const st = toLower(real.runStatus?.state)
  return st === 'stopped' || st === 'error'
})

const interactSelectedLink = computed<GraphLink | null>(() => {
  const from = interact.mode.state.fromPid
  const to = interact.mode.state.toPid
  if (!from || !to) return null

  // NEW-3: prefer backend-fetched trustlines (Interact Mode cache) as the source of truth.
  // This keeps EdgeDetailPopup and TrustlineManagementPanel consistent.
  const tls = interact.mode.trustlines.value
  if (Array.isArray(tls) && tls.length > 0) {
    const tl = tls.find((t) => t.from_pid === from && t.to_pid === to) ?? null
    if (tl) {
      return {
        source: from,
        target: to,
        trust_limit: tl.limit,
        used: tl.used,
        reverse_used: tl.reverse_used,
        available: tl.available,
        status: tl.status ?? undefined,
      }
    }
  }

  // Fallback: snapshot link (may be stale, but always available in fixtures/topology-only views).
  const snap = state.snapshot
  if (!snap) return null
  for (const l of snap.links ?? []) {
    if (l.source === from && l.target === to) return l
  }
  return null
})

function formatDemoActionError(e: unknown): string {
  const msg = extractErrorMessage(e)
  // Structured API errors may carry a `bodyText` field with the raw response body.
  const hasBodyText = e !== null && typeof e === 'object' && 'bodyText' in e
  const bodyText = hasBodyText ? String((e as Record<string, unknown>).bodyText ?? '').trim() : ''
  return bodyText ? `${msg}\n${bodyText}` : msg
}

const demoRunTxOnce = async () => {
  return runDemoFxOnce(() => fxDebug.runTxOnce())
}

const demoRunClearingOnce = async () => {
  return runDemoFxOnce(() => fxDebug.runClearingOnce())
}

async function runDemoFxOnce(action: () => Promise<void>): Promise<void> {
  if (fxDebug.busy.value) return
  real.lastError = ''
  try {
    await action()
  } catch (e: unknown) {
    real.lastError = formatDemoActionError(e)
  }
}

function setQueryAndReload(mut: (sp: URLSearchParams) => void) {
  const u = new URL(window.location.href)
  mut(u.searchParams)
  // Always strip legacy `devtools` param — it must never propagate across reloads.
  u.searchParams.delete('devtools')
  const nextHref = u.toString()
  // Setting href ensures full re-init (important when switching between pipelines).
  window.location.href = nextHref
}

function forceDbEnrichedPreviewOnNextLoad() {
  // Avoid a transient topology-only preview snapshot right after a full reload.
  // The real-mode boot flow shows a scenario preview until an active run is discovered.
  // If persisted desiredMode is "fixtures", the preview looks "non-enriched" for a few seconds.
  // TD-1: delegated to composable — no direct localStorage access.
  simulatorStorage.forceDesiredModeReal()
}

function clearFxDebugRunOnNextLoad() {
  // FX Debug may autostart a fixtures run and persist its runId.
  // After exiting Demo UI we want to return to the normal real UI preview (DB-enriched),
  // not a topology-only fixtures run snapshot.
  // TD-1: delegated to composable — no direct localStorage access.
  simulatorStorage.clearFxDebugRunState()
}

function enterDemoUi() {
  forceDbEnrichedPreviewOnNextLoad()

  // Demo enter: snapshot real-state so it can be restored after a reload-based exit.
  // Persisted snapshot must survive reload.
  simulatorStorage.writeDevtoolsOpenRealSnapshot(simulatorStorage.readDevtoolsOpenReal() ?? false)

  setQueryAndReload((sp) => {
    sp.set('mode', 'real')
    sp.set('ui', 'demo')
    sp.set('debug', '1')
  })
}

const isExiting = ref(false)

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

  // IMPORTANT: WM anchors must be in host-relative coordinates.
  // Prefer measuring the actual ActionBar element so interact panels open near the toolbar,
  // not at the far right edge of the viewport.
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

  // Anchor at the left edge under the bar so the panel opens near ActionBar,
  // not docked to the far right.
  const x = r.left - hostRect.left
  const y = r.bottom - hostRect.top
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null
  return { x, y }
}

/** Computed: should TrustlineManagementPanel be shown? */
const showTrustlinePanel = computed(() => {
  if (activePanelType.value !== 'trustline') return false
  // For editing-trustline phase, show panel only if explicitly requested
  if (interactPhase.value === 'editing-trustline') return useFullTrustlineEditor.value
  // For all other trustline phases (picking, confirm-create, etc.) always show the panel
  return true
})

async function exitDemoUi() {
  isExiting.value = true
  forceDbEnrichedPreviewOnNextLoad()

  // Best-effort cleanup: if Demo UI auto-started an FX debug run, stop it server-side
  // before doing a full reload, otherwise it may keep running in the background.
  // TD-1: delegated to composable — no direct localStorage access.
  try {
    if (simulatorStorage.isFxDebugRun()) {
      const stopPromise = realActions.stop().catch(() => {
        // ignore
      })
      await Promise.race([stopPromise, new Promise<void>((resolve) => setTimeout(resolve, 1500))])
    }
  } catch {
    // ignore
  } finally {
    isExiting.value = false
  }

  clearFxDebugRunOnNextLoad()

  // Demo exit: restore real-state from snapshot and clear snapshot.
  try {
    const snap = simulatorStorage.readDevtoolsOpenRealSnapshot()
    if (snap != null) simulatorStorage.writeDevtoolsOpenReal(snap)
  } finally {
    simulatorStorage.clearDevtoolsOpenRealSnapshot()
  }

  setQueryAndReload((sp) => {
    // Always exit into real full UI.
    sp.set('mode', 'real')
    sp.delete('ui')
    sp.delete('debug')
  })
}

function toggleDemoUi() {
  if (isDemoUi.value) void exitDemoUi()
  else enterDemoUi()
}

function goSandbox() {
  if (isInteractActivePhase.value) interact.mode.cancel()
  setQueryAndReload((sp) => {
    sp.set('mode', 'fixtures')
    sp.delete('ui')
    sp.delete('debug')
  })
}

function goAutoRun() {
  if (isInteractActivePhase.value) interact.mode.cancel()
  setQueryAndReload((sp) => {
    sp.set('mode', 'real')
    sp.delete('ui')
    sp.delete('debug')
  })
}

function goInteract() {
  if (isInteractUi.value) return
  setQueryAndReload((sp) => {
    sp.set('mode', 'real')
    sp.set('ui', 'interact')
    sp.delete('debug')
  })
}

// Interact Mode state is provided by useSimulatorApp() (core-only; panels/picking wiring is a later task).

 function onEdgeDetailChangeLimit() {
  // MUST: anchor propagation from edge popup to interact-panel.
  wmEdgePopupAnchor.value = interact.mode.state.edgeAnchor ?? null

   // Audit fix L-4 / Step 5: close ONLY the edge-detail inspector window, then open trustline.
   wmEdgeDetail.close({ suppress: false })
   wmEdgeDetail.applyToWindowManager(wm, { closeReason: 'programmatic' })

  // Switch from EdgeDetailPopup (quick info) to TrustlineManagementPanel (full editor).
  useFullTrustlineEditor.value = true
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
  void interact.mode.confirmTrustlineClose()
}

 function onEdgeDetailSendPayment() {
  // trustline from→to => payment to→from
  const fromPid = interact.mode.state.fromPid
  const toPid = interact.mode.state.toPid
  if (!fromPid || !toPid) return

  // MUST: if initiated from edge popup, propagate edge anchor to interact-panel.
  wmEdgePopupAnchor.value = interact.mode.state.edgeAnchor ?? null

   // KeepAlive: edge-detail stays open as context for the payment flow.
   // Spec: "инспектор остаётся открыт как «база контекста», а Interact-панель
   // открывается поверх для выполнения действия (Send Payment)."
   // Freeze the current link data before cancel() clears interact state.
   wmEdgeDetail.allowKeepAlive({ frozenLink: interactSelectedLink.value })
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
  // Not an edge-popup initiated action.
  wmEdgePopupAnchor.value = null
  // Snapshot NodeCard node screen center BEFORE phase changes.
  const snapshot = snapshotNodeCenter()

  if (opts.openEditor) useFullTrustlineEditor.value = true
  // H-1 coexistence: NodeCard is an inspector window and must coexist with interact.
  // WM: set anchor BEFORE phase change so the first `wm.open()` uses it.
  wmPanelOpenAnchor.value = snapshot
  opts.start()
}

function startFlowFromActionBar(opts: { openEditor?: boolean; start: () => void }) {
  // Not an edge-popup initiated action.
  wmEdgePopupAnchor.value = null
  // Snapshot ActionBar anchor BEFORE opts.start() changes the phase.
  const snapshot = getActionBarAnchor()
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

// When phase changes, reset local UI flags.
// Reset useFullTrustlineEditor when leaving trustline phases (idle, payment, clearing)
// so the NEXT edge click shows EdgeDetailPopup (not full editor).
// NOTE: do NOT reset when transitioning between trustline sub-phases
// (e.g. picking-trustline-to → editing-trustline) to preserve the ActionBar intent.
watch(interactPhase, (phase) => {
  const p = toLower(phase)
  if (!p.includes('trustline')) {
    useFullTrustlineEditor.value = false
  }
})
</script>

<template>
  <div
    ref="hostEl"
    class="root ds-ov-vars"
    :data-theme="uiTheme"
    data-density="comfortable"
    data-motion="full"
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
    <canvas ref="fxCanvasEl" class="canvas canvas-fx" />

    <div ref="dragPreviewEl" class="drag-preview" aria-hidden="true" />

    <EdgeTooltip
      :edge="hoveredEdge"
      :style="edgeTooltipStyle"
      :get-node-name="(id) => getNodeById(id)?.name ?? null"
      :interact-mode="isInteractUi"
    />

    <TopBar
      @go-sandbox="goSandbox"
      @go-auto-run="goAutoRun"
      @go-interact="goInteract"
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
      @update:desired-mode="realActions.setDesiredMode"
      @update:intensity-percent="realActions.setIntensityPercent"
    >
      <!-- HUD elements stacked below TopBar rows (inside ds-ov-top-stack via slot) -->
      <template v-if="dataReady && (isInteractUi || isAutoRunUi)">
        <SystemBalanceBar
          :balance="interact.systemBalance"
          :equivalent="effectiveEq"
          :compact="isAutoRunUi"
        />

        <ActionBar
          v-if="apiMode === 'real' && isInteractUi"
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
    </TopBar>

    <!-- Step 2: WindowLayer (renders migrated windows from WM state). -->
    <!-- R21: unified open/close transition for all windows. -->
    <!-- P1-3: @after-leave calls finishClose() to complete removal from WM map after animation. -->
    <TransitionGroup
      name="ws"
      @after-leave="(el) => { const id = parseInt((el as HTMLElement).dataset?.winId ?? ''); if (id) wm.finishClose(id) }"
      tag="div"
      class="wm-layer"
      aria-label="Window layer"
      :css="!isTestMode"
    >
      <!-- NB: @close is dead in frameless mode (no ws-close button rendered).
           Close is handled by child components (legacy ×, Cancel, Close).
           Keeping the handler for forward-compatibility if framed mode is re-enabled. -->
        <WindowShell
        v-for="win in wm.windows.value"
        :key="win.id"
        :instance="win"
        :title="wmTitleFor(win)"
        :frameless="true"
        @close="win.type === 'edge-detail' ? uiCloseEdgeDetailWindow('action') : wm.close(win.id, 'action')"
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
          :unit="effectiveEq"
          :used="emptyToNull(wmEdgeDetailEffectiveLink?.used)"
          :reverse-used="emptyToNull(wmEdgeDetailEffectiveLink?.reverse_used)"
          :limit="emptyToNull(wmEdgeDetailEffectiveLink?.trust_limit)"
          :available="emptyToNull(wmEdgeDetailEffectiveLink?.available)"
          :status="emptyToNullString(wmEdgeDetailEffectiveLink?.status)"
          :busy="wmEdgeDetailEffectiveBusy"
          :force-hidden="false"
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
          :show-pin-actions="!isTestMode && !isWebDriver"
          :is-pinned="isNodePinned(String(win.data.nodeId))"
          :pin="() => pinNode(String(win.data.nodeId))"
          :unpin="() => unpinNode(String(win.data.nodeId))"
          :interact-mode="isInteractUi"
          :interact-trustlines="isInteractUi ? interact.mode.trustlines.value : undefined"
          :trustlines-loading="isInteractUi ? interact.mode.trustlinesLoading.value : undefined"
          :interact-busy="isInteractUi ? interact.mode.busy.value : undefined"
          :on-interact-send-payment="isInteractUi ? onInteractSendPayment : undefined"
          :on-interact-new-trustline="isInteractUi ? onInteractNewTrustline : undefined"
          :on-interact-edit-trustline="isInteractUi ? onInteractEditTrustline : undefined"
          @close="() => {
            wm.close(win.id, 'action')
          }"
        />

        <ManualPaymentPanel
          v-else-if="isWmInteractPanelWindow(win, 'payment')"
          :phase="wmInteractPanelPhase(win)"
          :state="interact.mode.state"
          :unit="effectiveEq"
          :available-capacity="interact.mode.availableCapacity.value"
          :trustlines-loading="trustlinesLoading"
          :payment-targets-loading="paymentTargetsLoading"
          :payment-targets-max-hops="paymentTargetsMaxHops"
          :payment-to-target-ids="paymentToTargetIds"
          :trustlines="trustlines"
          :trustlines-last-error="trustlinesLastError"
          :payment-targets-last-error="paymentTargetsLastError"
          :participants="interact.mode.participants.value"
          :busy="interact.mode.busy.value"
          :can-send-payment="interact.mode.canSendPayment.value"
          :confirm-payment="interact.mode.confirmPayment"
          :set-from-pid="interact.mode.setPaymentFromPid"
          :set-to-pid="interact.mode.setPaymentToPid"
          :cancel="interact.mode.cancel"
        />

        <TrustlineManagementPanel
          v-else-if="isWmInteractPanelWindow(win, 'trustline')"
          ref="tlPanel"
          :phase="wmInteractPanelPhase(win)"
          :state="interact.mode.state"
          :unit="effectiveEq"
          :used="emptyToNull(interactSelectedLink?.used)"
          :current-limit="emptyToNull(interactSelectedLink?.trust_limit)"
          :available="emptyToNull(interactSelectedLink?.available)"
          :participants="interact.mode.participants.value"
          :trustlines="interact.mode.trustlines.value"
          :busy="interact.mode.busy.value"
          :confirm-trustline-create="interact.mode.confirmTrustlineCreate"
          :confirm-trustline-update="interact.mode.confirmTrustlineUpdate"
          :confirm-trustline-close="interact.mode.confirmTrustlineClose"
          :set-from-pid="interact.mode.setTrustlineFromPid"
          :set-to-pid="interact.mode.setTrustlineToPid"
          :select-trustline="interact.mode.selectTrustline"
          :cancel="interact.mode.cancel"
        />

        <ClearingPanel
          v-else-if="isWmInteractPanelWindow(win, 'clearing')"
          :phase="wmInteractPanelPhase(win)"
          :state="interact.mode.state"
          :busy="interact.mode.busy.value"
          :equivalent="effectiveEq"
          :confirm-clearing="interact.mode.confirmClearing"
          :cancel="interact.mode.cancel"
        />
      </WindowShell>
    </TransitionGroup>
    <!-- E2E screenshot tests: minimal offline controls only (match stored snapshots). -->
    <div v-if="isE2eScreenshots" class="ds-ov-bottom ds-panel ds-ov-bar">
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
      :active-segment="activeSegment"
      :is-demo-fixtures="isDemoFixtures"
      :show-reset-view="showResetView"
      :reset-view="resetView"
      :run-id="real.runId"
      :refresh-snapshot="realActions.refreshSnapshot"
      :artifacts="real.artifacts"
      :artifacts-loading="real.artifactsLoading"
      :refresh-artifacts="realActions.refreshArtifacts"
      :download-artifact="realActions.downloadArtifact"
      :is-web-driver="isWebDriver"
      :is-test-mode="isTestMode"
      :is-e2e-screenshots="isE2eScreenshots"
      :is-demo-ui="isDemoUi"
      :is-exiting="isExiting"
      :toggle-demo-ui="toggleDemoUi"
      :fx-debug-enabled="apiMode === 'real' && fxDebug.enabled.value"
      :fx-busy="fxDebug.busy.value"
      :run-tx-once="isDemoUi ? demoRunTxOnce : fxDebug.runTxOnce"
      :run-clearing-once="isDemoUi ? demoRunClearingOnce : fxDebug.runClearingOnce"
    />

    <!-- Loading / error overlay (fail-fast, but non-intrusive).
         Hide the overlay during incremental updates (when we already have a snapshot)
         to avoid a visible "Loading…" flash on preview → run transitions. -->
    <div v-if="state.loading && !state.snapshot" class="ds-ov-inset">
      <div class="ds-ov-message">
        <div class="ds-ov-message__title">Loading…</div>
      </div>
    </div>
    <div v-else-if="state.error" class="ds-ov-inset">
      <div class="ds-ov-message">
        <div class="ds-ov-message__title">Error</div>
        <div class="ds-ov-message__text">{{ state.error }}</div>
      </div>
    </div>

    <LabelsOverlayLayers
      :label-nodes="labelNodes"
      :floating-labels="floatingLabelsViewFx"
      :world-to-css-translate-no-scale="worldToCssTranslateNoScale"
    />

    <DevPerfOverlay :enabled="showPerfOverlay" :perf="perf" />

    <!-- BUG-6: Error toast for Interact Mode (auto-dismiss 4s). -->
    <SuccessToast
      v-if="isInteractUi"
      :message="interact.mode.successMessage"
      @dismiss="interact.mode.successMessage.value = null"
    />

    <ErrorToast
      v-if="isInteractUi"
      :message="interact.mode.state.error"
      @dismiss="interact.mode.state.error = null"
    />

    <!-- BUG-5: Interact Mode inline history log. -->
    <div
      v-if="isInteractUi && interact.mode.history.length > 0"
      class="ds-ov-bottom sar-interact-history-overlay"
    >
      <InteractHistoryLog :entries="interact.mode.history" :max-visible="8" />
    </div>
  </div>
</template>

<style scoped>
.wm-layer {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: var(--ds-z-panel);
}

.wm-layer :deep(.ws-shell) {
  pointer-events: auto;
}

.sar-interact-history-overlay {
  right: var(--ds-sar-interact-history-right);
  left: auto;
  bottom: var(--ds-sar-interact-history-bottom);
  padding: var(--ds-sar-interact-history-pad-y) var(--ds-sar-interact-history-pad-x);
  pointer-events: none;
}
</style>
