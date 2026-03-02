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
 import { useInteractPanelPosition } from '../composables/useInteractPanelPosition'
 import type { Point } from '../composables/useInteractPanelPosition'
 import { useSimulatorStorage } from '../composables/usePersistedSimulatorPrefs'
 import { normalizeUiThemeId, type UiThemeId } from '../types/uiPrefs'
 import { toLower } from '../utils/stringHelpers'

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
})

onUnmounted(() => {
  window.removeEventListener('popstate', syncThemeFromUrl)
})

import type { GraphLink } from '../types'

import { useSimulatorApp } from '../composables/useSimulatorApp'
import { emptyToNull, emptyToNullString } from '../utils/valueFormat'
import { handleEscOverlayStack } from '../utils/escOverlayStack'
import { buildReloadUrlPreservingWmOptOut } from '../utils/navigationUrl'

  import { useWindowManager } from '../composables/windowManager/useWindowManager'
  import type { WindowInstance } from '../composables/windowManager/types'
import type { WindowAnchor } from '../composables/windowManager/types'
import { isInteractPanelWindow, isNodeCardWindow } from '../composables/windowManager/types'
import { interactWindowOfPhase } from '../composables/windowManager/interactWindowOfPhase'
import {
  provideWindowManagerEnabled,
  readWindowManagerEnabledFromUrl,
} from '../composables/windowManager/featureFlag'

// Step 2 runtime flag (default false): enable via query string `?wm=1`.
const __USE_WINDOW_MANAGER: boolean = readWindowManagerEnabledFromUrl()

// Expose WM enabled state to composables (avoid direct window.location reads).
provideWindowManagerEnabled(__USE_WINDOW_MANAGER)

function readWindowViewportFallback(): { width: number; height: number } {
  try {
    const w = (globalThis as any).window as any
    const width = Number(w?.innerWidth)
    const height = Number(w?.innerHeight)
    return {
      width: Number.isFinite(width) && width > 0 ? width : 1280,
      height: Number.isFinite(height) && height > 0 ? height : 720,
    }
  } catch {
    return { width: 1280, height: 720 }
  }
}

const wm = useWindowManager()

// Step 5: inspector window ids (singleton=reuse → stable across updates).
const wmEdgeDetailId = ref<number | null>(null)
const wmNodeCardId = ref<number | null>(null)

// H-3: UI-close vs Flow-cancel for edge-detail.
// EdgeDetailPopup is derived from Interact FSM state (editing-trustline + edgeAnchor).
// In WM mode, UI-close must hide the *window* without cancelling the flow.
// We implement a UI-only suppression flag that blocks auto-open until selection changes.
const wmEdgeDetailSuppressed = ref(false)
const wmEdgeDetailSelectionKey = ref<string>('')

// KeepAlive: when Send Payment is initiated from edge-detail, the edge-detail window
// persists as context (inspector coexists with interact-panel). The watcher must NOT
// auto-close it, and the component receives frozen link data instead of live interact state.
const wmEdgeDetailKeepAlive = ref(false)
const wmEdgeDetailFrozenLink = ref<GraphLink | null>(null)

function wmResetEdgeDetailKeepAlive() {
  wmEdgeDetailKeepAlive.value = false
  wmEdgeDetailFrozenLink.value = null
}

function uiCloseEdgeDetailWindow(winId: number, reason: 'action' | 'programmatic') {
  if (!__USE_WINDOW_MANAGER) return
  wmEdgeDetailSuppressed.value = true
  wmResetEdgeDetailKeepAlive()
  wm.close(winId, reason)
  if (wmEdgeDetailId.value === winId) wmEdgeDetailId.value = null
}

function uiCloseTopmostInspectorWindow(): 'edge-detail' | 'node-card' | null {
  if (!__USE_WINDOW_MANAGER) return null

  // WM source of truth: still query the topmost/active inspector window by WM z-order.
  // (Useful for tests and for any future "close-only-topmost" variants.)
  const top = wm.getTopmostInGroup('inspector')
  const topType: 'edge-detail' | 'node-card' | null =
    top && top.policy.closeOnOutsideClick && (top.type === 'edge-detail' || top.type === 'node-card')
      ? top.type
      : null

  // AC-4 / Option C: outside-click is a "hard" dismiss.
  // Close all windows and cancel any active Interact flow to avoid orphaned state.

  // Clear keepAlive so frozen inspectors can't persist after an outside-click.
  wmResetEdgeDetailKeepAlive()
  // Outside-click is not a UI-close suppression; the flow is cancelled anyway.
  if (wmEdgeDetailSuppressed.value) wmEdgeDetailSuppressed.value = false
  if (wmEdgeDetailSelectionKey.value) wmEdgeDetailSelectionKey.value = ''

  // Best-effort: close both groups. Programmatic close must NOT trigger interact.onClose.
  wm.closeGroup('inspector', 'programmatic')
  wm.closeGroup('interact', 'programmatic')
  wmEdgeDetailId.value = null
  wmNodeCardId.value = null

  // Cancel the flow explicitly (WM programmatic close does not call interact-panel onClose).
  try {
    interact.mode.cancel()
  } catch {
    // best-effort
  }

  return topType
}

function uiOpenOrUpdateEdgeDetail(o: { fromPid: string; toPid: string; anchor: Point }) {
  if (!__USE_WINDOW_MANAGER) return
  const id = wm.open({
    type: 'edge-detail',
    anchor: toWmAnchor(o.anchor, 'edge-click'),
    data: { fromPid: o.fromPid, toPid: o.toPid },
  })
  wmEdgeDetailId.value = id
}

function uiOpenOrUpdateNodeCard(o: { nodeId: string; anchor: Point | null }) {
  if (!__USE_WINDOW_MANAGER) return
  const id = wm.open({
    type: 'node-card',
    anchor: toWmAnchor(o.anchor, 'node'),
    data: { nodeId: o.nodeId },
  })
  wmNodeCardId.value = id
}

const app = useSimulatorApp({
  uiCloseTopmostInspectorWindow: __USE_WINDOW_MANAGER ? uiCloseTopmostInspectorWindow : undefined,
  uiOpenOrUpdateEdgeDetail: __USE_WINDOW_MANAGER ? uiOpenOrUpdateEdgeDetail : undefined,
  uiOpenOrUpdateNodeCard: __USE_WINDOW_MANAGER ? uiOpenOrUpdateNodeCard : undefined,
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
  isNodeCardOpen,
  setNodeCardOpen,
  hoveredEdge,
  clearHoveredEdge,
  edgeTooltipStyle: calcEdgeTooltipStyle,
  selectedNode,
  nodeCardStyle,
  nodeCardCardRef,
  selectedNodeScreenCenter,
  selectedNodeEdgeStats,

  // pinning
  dragToPin,
  isSelectedPinned,
  pinSelectedNode,
  unpinSelectedNode,

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

/**
 * Централизованное управление anchor-позицией Interact-панелей.
 * (Legacy panels still use `panelAnchor`; WM uses it as a fallback when
 * there is no edge-popup initiated anchor.)
 */
 const { panelAnchor, openFrom: openPanelFrom } = useInteractPanelPosition(interactPhase)

const legacyShowManualPaymentPanel = computed(
  () => apiMode.value === 'real' && isInteractUi.value && activePanelType.value === 'payment',
)

/**
 * Step 4: Interact → WindowManager bridging.
 *
 * - interactPhase changes → WM opens/updates interact-panel window for {payment|trustline|clearing}
 * - Otherwise → closes interact group.
 *
 * Anchor propagation rule (MUST):
 * If action initiated from EdgeDetailPopup (edge popup) opens an interact-panel,
 * the window MUST receive anchor = state.edgeAnchor.
 */
 const wmEdgePopupAnchor = ref<Point | null>(null)

/**
 * WM-only anchor for opening an interact-panel from NodeCard / ActionBar.
 *
 * Why: legacy `panelAnchor` is cleared synchronously on panel-group changes.
 * In WM mode, that can cause a 2-step `wm.open()` sequence (first with null anchor,
 * then re-open/reposition with the intended anchor), which is visible as a
 * “transient intermediate window”.
 */
const wmPanelOpenAnchor = ref<Point | null>(null)

 function makeInteractPanelWindowData(panel: 'payment' | 'trustline' | 'clearing', phase: string) {
   const onBack = (): boolean => {
     // Step-back inside Interact FSM (only when it has a meaningful previous step).
     // MUST: do not perform Flow-cancel here; fallback to UI-close when no step-back exists.
     const p = String(interactPhase.value) as InteractPhase

     // Payment: confirm → picking-to → (picking-from only if FROM was not pre-filled)
     if (p === 'confirm-payment') {
       interact.mode.setPaymentToPid(null)
       return true
     }
     if (p === 'picking-payment-to') {
       // If the flow was initiated with pre-filled FROM (NodeCard/EdgeDetail/etc),
       // there is no meaningful "previous step" — close the window instead.
       if (interact.mode.state.initiatedWithPrefilledFrom) return false
       interact.mode.setPaymentFromPid(null)
       return true
     }

     // Trustline: confirm/edit → picking-to → (picking-from only if FROM was not pre-filled)
     if (p === 'editing-trustline' || p === 'confirm-trustline-create') {
       // Always step back to picking-to by clearing TO.
       interact.mode.setTrustlineToPid(null)
       return true
     }
     if (p === 'picking-trustline-to') {
       // If the flow was initiated with pre-filled FROM (NodeCard/EdgeDetail/etc),
       // there is no meaningful "previous step" — close the window instead.
       if (interact.mode.state.initiatedWithPrefilledFrom) return false
       interact.mode.setTrustlineFromPid(null)
       return true
     }

     return false
   }

   return { panel, phase, onBack, onClose: () => interact.mode.cancel() }
 }

function toWmAnchor(p: Point | null, source: string): WindowAnchor | null {
  if (!p) return null
  return { x: p.x, y: p.y, space: 'host', source }
}

const wmInteractAnchor = computed<WindowAnchor | null>(() => {
  // Edge popup anchor has priority (normative requirement).
  if (wmEdgePopupAnchor.value) return toWmAnchor(wmEdgePopupAnchor.value, 'edge-popup')

  // NodeCard / ActionBar initiated anchors should apply to the first WM open.
  if (wmPanelOpenAnchor.value) return toWmAnchor(wmPanelOpenAnchor.value, 'panel')

  // Fallback: generic panel anchor (ActionBar / NodeCard positioning).
  return toWmAnchor(panelAnchor.value ?? null, 'panel')
})

// IMPORTANT: avoid watching the anchor object by identity.
// `toWmAnchor()` creates a new object, so a `watch(() => ({ anchor }))` would retrigger
// even when x/y didn't change, causing duplicate `wm.open()` calls while the window
// is still unmeasured (visible as a position jump).
const wmInteractAnchorKey = computed(() => {
  const a = wmInteractAnchor.value
  if (!a) return ''
  return `${a.x}|${a.y}|${a.space}|${a.source}`
})

// IMPORTANT (Vue reactivity): use `watch` instead of `watchEffect` here.
// `wm.open()` reads WM reactive state internally (windowsMap, viewport, idCounter).
// If called inside a watchEffect, Vue tracks those reads as dependencies and can trigger
// a recursive update loop when a window is opened. Explicit watch avoids this.
// Same pattern as the inspector watcher below (line ~375).
watch(
  [
    () => __USE_WINDOW_MANAGER,
    () => apiMode.value,
    () => isInteractUi.value,
    () => String(interactPhase.value),
    () => useFullTrustlineEditor.value,
    () => wmInteractAnchorKey.value,
  ],
  ([enabled, curApiMode, curIsInteractUi, phase, isFullEditor]) => {
    if (!enabled) return

    // Safety: only drive Interact windows in real interact UI.
    if (curApiMode !== 'real' || !curIsInteractUi) {
      wm.closeGroup('interact', 'programmatic')
      wmPanelOpenAnchor.value = null
      return
    }

    const m = interactWindowOfPhase(phase, isFullEditor)
    if (m?.type === 'interact-panel') {
      wm.open({
        type: 'interact-panel',
        anchor: wmInteractAnchor.value,
        data: makeInteractPanelWindowData(m.panel, phase),
      })
      return
    }

    // idle / inspector-only phases: ensure interact window is closed.
    wm.closeGroup('interact', 'programmatic')

    // Avoid stale anchors after closing the group.
    wmEdgePopupAnchor.value = null
    wmPanelOpenAnchor.value = null
  },
  { immediate: true },
)

/**
 * Step 5: Inspector → WindowManager bridging.
 *
 * - `editing-trustline` quick-info → open/update edge-detail inspector.
 * - Node card open signal (legacy selection state) → open/update node-card inspector.
 */
// IMPORTANT (Vue reactivity): do NOT use watchEffect here.
// `wm.open()` reads WM reactive state internally; if called inside a watchEffect,
// Vue can track WM state as a dependency, causing recursive update loops.
watch(
  () => ({
    enabled: __USE_WINDOW_MANAGER,
    apiMode: apiMode.value,
    isInteractUi: isInteractUi.value,
    phase: String(interactPhase.value),
    isFullEditor: useFullTrustlineEditor.value,
    anchor: interact.mode.state.edgeAnchor,
    fromPid: interact.mode.state.fromPid,
    toPid: interact.mode.state.toPid,
    suppressed: wmEdgeDetailSuppressed.value,
  }),
  (s) => {
    if (!s.enabled) return

    // Reset UI-close suppression on selection changes.
    // (When the same edge is re-selected, anchor typically changes; include anchor in the key.)
    const key = s.anchor
      ? `${String(s.fromPid ?? '')}→${String(s.toPid ?? '')}@${String((s.anchor as any)?.x ?? '')},${String((s.anchor as any)?.y ?? '')}`
      : ''
    if (key && key !== wmEdgeDetailSelectionKey.value) {
      wmEdgeDetailSelectionKey.value = key
      if (wmEdgeDetailSuppressed.value) wmEdgeDetailSuppressed.value = false
    }

    if (s.phase !== 'editing-trustline') {
      // Outside of edge-detail phase, the suppression is irrelevant.
      if (wmEdgeDetailSuppressed.value) wmEdgeDetailSuppressed.value = false
      wmEdgeDetailSelectionKey.value = ''
    }

    const shouldShow =
      s.apiMode === 'real' &&
      s.isInteractUi &&
      s.phase === 'editing-trustline' &&
      !s.isFullEditor &&
      !s.suppressed &&
      !!s.anchor &&
      !!s.fromPid &&
      !!s.toPid

    if (shouldShow) {
      wmEdgeDetailId.value = wm.open({
        type: 'edge-detail',
        anchor: toWmAnchor(s.anchor ?? null, 'interact-state'),
        data: {
          fromPid: String(s.fromPid),
          toPid: String(s.toPid),
          onClose: () => wmResetEdgeDetailKeepAlive(),
        },
      })
      return
    }

    // KeepAlive: when a flow was initiated from edge-detail (e.g. Send Payment),
    // the edge-detail window persists as context. Don't auto-close it.
    if (wmEdgeDetailKeepAlive.value && wmEdgeDetailId.value != null) {
      return
    }

    if (wmEdgeDetailId.value != null) {
      wm.close(wmEdgeDetailId.value, 'programmatic')
      wmEdgeDetailId.value = null
    }
  },
  { immediate: true },
)

watch(
  () => ({
    enabled: __USE_WINDOW_MANAGER,
    open: isNodeCardOpen.value,
    nodeId: selectedNode.value?.id ?? null,
    anchor: selectedNodeScreenCenter.value,
    dragging: !!dragToPin.dragState.active,
  }),
  (s) => {
    if (!s.enabled) return

    const shouldShow = !!s.nodeId && s.open && !s.dragging
    if (shouldShow) {
      wmNodeCardId.value = wm.open({
        type: 'node-card',
        anchor: toWmAnchor(s.anchor ?? null, 'node'),
        data: {
          nodeId: String(s.nodeId),
          // Bridge WM-driven UI-close back into legacy open flag.
          onClose: () => setNodeCardOpen(false),
        },
      })
      return
    }

    if (wmNodeCardId.value != null) {
      wm.close(wmNodeCardId.value, 'programmatic')
      wmNodeCardId.value = null
    }
  },
  { immediate: true },
)

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
  if (wmEdgeDetailKeepAlive.value && wmEdgeDetailFrozenLink.value != null) {
    return wmEdgeDetailFrozenLink.value
  }
  return interactSelectedLink.value
})
const wmEdgeDetailEffectivePhase = computed<InteractPhase>(() => {
  if (wmEdgeDetailKeepAlive.value) return 'editing-trustline' as InteractPhase
  return interactPhase.value
})
const wmEdgeDetailEffectiveBusy = computed(() => {
  if (wmEdgeDetailKeepAlive.value) return true // disable action buttons while parent flow is active
  return interact.mode.busy.value
})

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

function isFormLikeTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null
  const tag = toLower((el as any)?.tagName)
  return tag === 'input' || tag === 'textarea' || tag === 'select'
}

function dispatchInteractEsc(): boolean {
  try {
    const escEvt = new CustomEvent('geo:interact-esc', { cancelable: true })
    return window.dispatchEvent(escEvt)
  } catch {
    return true
  }
}

function onGlobalKeydown(ev: KeyboardEvent) {
  // Step WM-ESC: when WM is active, delegate ESC handling to wm.handleEsc() first.
  // wm.handleEsc() respects per-window escBehavior (close / back-then-close / ignore)
  // and gives nested content a chance to consume ESC via dispatchInteractEsc.
  if (__USE_WINDOW_MANAGER && (ev.key === 'Escape' || ev.key === 'Esc')) {
    const consumed = wm.handleEsc(ev, {
      isFormLikeTarget,
      dispatchWindowEsc: dispatchInteractEsc,
    })
    if (consumed) return

    // WM mode: do NOT fall back to legacy ESC overlay stack.
    // Legacy stack would "magically" close WM-owned windows (NodeCard/EdgeDetail/etc)
    // by calling setNodeCardOpen(false) and other non-WM closures.
    return
  }

  handleEscOverlayStack(ev, {
    isNodeCardOpen: () => isNodeCardOpen.value,
    closeNodeCard: () => setNodeCardOpen(false),

    isInteractActive: () => isInteractActivePhase.value,
    cancelInteract: () => interact.mode.cancel(),

    isFormLikeTarget,
    dispatchInteractEsc,
  })
}

onMounted(() => {
  window.addEventListener('keydown', onGlobalKeydown)

  // Step 3: Viewport ResizeObserver wiring (WM only).
  // Keep an initial snapshot as a fallback, but use RO as the main source of viewport updates.
  if (__USE_WINDOW_MANAGER) {
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

function formatDemoActionError(e: any): string {
  const msg = String(e?.message ?? e)
  const body = typeof e?.bodyText === 'string' && e.bodyText.trim() ? `\n${e.bodyText.trim()}` : ''
  return `${msg}${body}`
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
  } catch (e: any) {
    real.lastError = formatDemoActionError(e)
  }
}

function setQueryAndReload(mut: (sp: URLSearchParams) => void) {
  const nextHref = buildReloadUrlPreservingWmOptOut(window.location.href, (sp) => {
    mut(sp)
    // Always strip legacy `devtools` param — it must never propagate across reloads.
    sp.delete('devtools')
  })
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

const tlPanel = ref<InstanceType<typeof TrustlineManagementPanel> | null>(null)

/**
 * Snapshot the selected node's screen-space center.
 * Must be called BEFORE closing the NodeCard (selectedNode is still valid at that point).
 * Falls back to parsing nodeCardStyle position if selectedNodeScreenCenter is unavailable.
 */
function snapshotNodeCenter(): Point | null {
  const center = selectedNodeScreenCenter.value
  if (center) return center
  // Fallback: parse card CSS position (top-left corner ≈ near node)
  const style = nodeCardStyle.value
  if (style.left && style.top) {
    const x = parseInt(style.left as string, 10)
    const y = parseInt(style.top as string, 10)
    if (Number.isFinite(x) && Number.isFinite(y)) return { x, y }
  }
  return null
}

/**
 * Returns an anchor for ActionBar-opened panels.
 *
 * Design goals:
 * - Panel appears in the top-right corner, directly under the ActionBar buttons.
 * - Stays within screen bounds regardless of viewport width.
 * - Does NOT rely on CSS `right: 12px` default — explicit inline style wins every time.
 *
 * Math (see placeOverlayNearAnchor in overlayPosition.ts):
 *   left = clamp(anchor.x + offX, pad, vw - panelW - pad)
 *   with anchor.x = rect.width, offX = 12, pad = 12, panelW = 560:
 *   → left = clamp(rect.width + 12, 12, rect.width - 572)
 *   → left = rect.width - 572  (right-edge of panel = rect.width - 12 ✓)
 *
 *   top = clamp(anchor.y + offY, pad, vh - panelH - pad)
 *   with anchor.y = 98, offY = 12:
 *   → top = 110px  (matches .ds-ov-panel CSS default, just below ActionBar)
 *
 * If hostEl is unavailable (SSR / tests), returns null → CSS default applies.
 */
function getActionBarAnchor(): Point | null {
  const host = hostEl.value
  if (!host) return null
  const rect = host.getBoundingClientRect()
  return { x: rect.width, y: 98 }
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
  if (__USE_WINDOW_MANAGER) {
    // MUST: anchor propagation from edge popup to interact-panel.
    wmEdgePopupAnchor.value = interact.mode.state.edgeAnchor ?? null

    // Audit fix L-4 / Step 5: close ONLY the edge-detail inspector window, then open trustline.
    if (wmEdgeDetailId.value != null) {
      wm.close(wmEdgeDetailId.value, 'programmatic')
      wmEdgeDetailId.value = null
    }
  }

  // Позиционируем TrustlineManagementPanel рядом с тем же ребром.
  openPanelFrom('change-limit', interact.mode.state.edgeAnchor ?? null)

  // Switch from EdgeDetailPopup (quick info) to TrustlineManagementPanel (full editor).
  useFullTrustlineEditor.value = true
  // Focus the limit editor in TrustlineManagementPanel via template ref.
  void nextTick(() => {
    const v = tlPanel.value as any
    if (typeof v?.focusNewLimit === 'function') {
      v.focusNewLimit()
      return
    }
    if (Array.isArray(v)) {
      const inst = v.find((x) => typeof x?.focusNewLimit === 'function')
      inst?.focusNewLimit?.()
    }
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
  if (__USE_WINDOW_MANAGER) {
    wmEdgePopupAnchor.value = interact.mode.state.edgeAnchor ?? null

    // KeepAlive: edge-detail stays open as context for the payment flow.
    // Spec: "инспектор остаётся открыт как «база контекста», а Interact-панель
    // открывается поверх для выполнения действия (Send Payment)."
    // Freeze the current link data before cancel() clears interact state.
    wmEdgeDetailKeepAlive.value = true
    wmEdgeDetailFrozenLink.value = interactSelectedLink.value
    // Do NOT close edge-detail — it persists as context.
  }

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
  if (__USE_WINDOW_MANAGER) {
    // Not an edge-popup initiated action.
    wmEdgePopupAnchor.value = null
  }
  // Snapshot node screen center BEFORE closing the card (selectedNode cleared after close).
  const snapshot = snapshotNodeCenter()

  if (opts.openEditor) useFullTrustlineEditor.value = true
  // H-1 coexistence: in WM mode NodeCard is an inspector window and must coexist with interact.
  if (!__USE_WINDOW_MANAGER) setNodeCardOpen(false)

  // WM: set anchor BEFORE phase change so the first `wm.open()` uses it.
  if (__USE_WINDOW_MANAGER) {
    wmPanelOpenAnchor.value = snapshot
  }
  opts.start()

  // Legacy panels: safe to set synchronously — flush:'sync' watcher in useInteractPanelPosition
  // already fired during opts.start() and cleared anchor for the group change.
  if (!__USE_WINDOW_MANAGER) {
    openPanelFrom('node-card', snapshot)
  }
}

function startFlowFromActionBar(opts: { openEditor?: boolean; start: () => void }) {
  if (__USE_WINDOW_MANAGER) {
    // Not an edge-popup initiated action.
    wmEdgePopupAnchor.value = null
  }
  // Snapshot ActionBar anchor BEFORE opts.start() changes the phase.
  const snapshot = getActionBarAnchor()
  // H-1 coexistence: do not auto-close inspector under WM.
  if (!__USE_WINDOW_MANAGER) setNodeCardOpen(false)
  if (opts.openEditor) useFullTrustlineEditor.value = true

  // WM: set anchor BEFORE phase change so the first `wm.open()` uses it.
  if (__USE_WINDOW_MANAGER) {
    wmPanelOpenAnchor.value = snapshot
  }
  opts.start()

  // Legacy panels: set top-right anchor explicitly — avoids relying on CSS `right: 12px` default
  // which could be overridden by a stale inline style from a previous node-card session.
  if (!__USE_WINDOW_MANAGER) {
    openPanelFrom('action-bar', snapshot)
  }
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
// Anchor clearing delegated to useInteractPanelPosition.
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
    class="root"
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
      <div v-if="dataReady && (isInteractUi || isAutoRunUi)" class="interact-hud-bar">
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
      </div>
    </TopBar>

  <Transition name="panel-slide" mode="out-in">
      <ManualPaymentPanel
        v-if="legacyShowManualPaymentPanel && !__USE_WINDOW_MANAGER"
        key="payment"
        :phase="interactPhase"
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
        :anchor="panelAnchor"
        :host-el="hostEl"
      />

      <TrustlineManagementPanel
        v-else-if="apiMode === 'real' && isInteractUi && showTrustlinePanel && !__USE_WINDOW_MANAGER"
        key="trustline"
        ref="tlPanel"
        :phase="interactPhase"
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
        :anchor="panelAnchor"
        :host-el="hostEl"
      />

      <ClearingPanel
        v-else-if="apiMode === 'real' && isInteractUi && activePanelType === 'clearing' && !__USE_WINDOW_MANAGER"
        key="clearing"
        :phase="interactPhase"
        :state="interact.mode.state"
        :busy="interact.mode.busy.value"
        :equivalent="effectiveEq"
        :confirm-clearing="interact.mode.confirmClearing"
        :cancel="interact.mode.cancel"
        :anchor="panelAnchor"
        :host-el="hostEl"
      />
    </Transition>

    <!-- Step 2: WindowLayer (renders migrated windows from WM state). -->
    <!-- R21: unified open/close transition for all windows. -->
    <TransitionGroup
      v-if="__USE_WINDOW_MANAGER"
      name="ws"
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
        @close="win.type === 'edge-detail' ? uiCloseEdgeDetailWindow(win.id, 'action') : wm.close(win.id, 'action')"
        @focus="wm.focus(win.id)"
        @measured="(s) => {
          wm.updateMeasuredSize(win.id, s)
          wm.reclamp(win.id)
        }"
      >
        <EdgeDetailPopup
          v-if="win.type === 'edge-detail'"
          :phase="wmEdgeDetailEffectivePhase"
          :state="interact.mode.state"
          :host-el="null"
          :unit="effectiveEq"
          :used="emptyToNull(wmEdgeDetailEffectiveLink?.used)"
          :reverse-used="emptyToNull(wmEdgeDetailEffectiveLink?.reverse_used)"
          :limit="emptyToNull(wmEdgeDetailEffectiveLink?.trust_limit)"
          :available="emptyToNull(wmEdgeDetailEffectiveLink?.available)"
          :status="emptyToNullString(wmEdgeDetailEffectiveLink?.status)"
          :busy="wmEdgeDetailEffectiveBusy"
          :force-hidden="false"
          render-mode="wm"
          :close="() => uiCloseEdgeDetailWindow(win.id, 'action')"
          @change-limit="onEdgeDetailChangeLimit"
          @close-line="onEdgeDetailCloseLine"
          @send-payment="onEdgeDetailSendPayment"
        />

        <NodeCardOverlay
          v-else-if="isNodeCardWindow(win) && getNodeById(String(win.data.nodeId))"
          :node="getNodeById(String(win.data.nodeId))!"
          :style="{}"
          :edge-stats="selectedNodeEdgeStats"
          :equivalent-text="state.snapshot?.equivalent ?? ''"
          :show-pin-actions="!isTestMode && !isWebDriver"
          :is-pinned="isSelectedPinned"
          :pin="pinSelectedNode"
          :unpin="unpinSelectedNode"
          :interact-mode="isInteractUi"
          :interact-trustlines="isInteractUi ? interact.mode.trustlines.value : undefined"
          :trustlines-loading="isInteractUi ? interact.mode.trustlinesLoading.value : undefined"
          :interact-busy="isInteractUi ? interact.mode.busy.value : undefined"
          :on-interact-send-payment="isInteractUi ? onInteractSendPayment : undefined"
          :on-interact-new-trustline="isInteractUi ? onInteractNewTrustline : undefined"
          :on-interact-edit-trustline="isInteractUi ? onInteractEditTrustline : undefined"
          render-mode="wm"
          @close="() => {
            wm.close(win.id, 'action')
            setNodeCardOpen(false)
          }"
        />

        <ManualPaymentPanel
          v-else-if="isWmInteractPanelWindow(win, 'payment')"
          render-mode="wm"
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
          :anchor="null"
          :host-el="null"
        />

        <TrustlineManagementPanel
          v-else-if="isWmInteractPanelWindow(win, 'trustline')"
          ref="tlPanel"
          render-mode="wm"
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
          :anchor="null"
          :host-el="null"
        />

        <ClearingPanel
          v-else-if="isWmInteractPanelWindow(win, 'clearing')"
          render-mode="wm"
          :phase="wmInteractPanelPhase(win)"
          :state="interact.mode.state"
          :busy="interact.mode.busy.value"
          :equivalent="effectiveEq"
          :confirm-clearing="interact.mode.confirmClearing"
          :cancel="interact.mode.cancel"
          :anchor="null"
          :host-el="null"
        />
      </WindowShell>
    </TransitionGroup>

    <EdgeDetailPopup
      v-if="apiMode === 'real' && isInteractUi && !__USE_WINDOW_MANAGER"
      :phase="interactPhase"
      :state="interact.mode.state"
      :host-el="hostEl"
      :unit="effectiveEq"
      :used="emptyToNull(interactSelectedLink?.used)"
      :reverse-used="emptyToNull(interactSelectedLink?.reverse_used)"
      :limit="emptyToNull(interactSelectedLink?.trust_limit)"
      :available="emptyToNull(interactSelectedLink?.available)"
      :status="emptyToNullString(interactSelectedLink?.status)"
      :busy="interact.mode.busy.value"
      :force-hidden="useFullTrustlineEditor"
      :close="interact.mode.cancel"
      @change-limit="onEdgeDetailChangeLimit"
      @close-line="onEdgeDetailCloseLine"
      @send-payment="onEdgeDetailSendPayment"
    />

    <NodeCardOverlay
      v-if="isNodeCardOpen && selectedNode && !dragToPin.dragState.active && !__USE_WINDOW_MANAGER"
      :ref="(el: any) => { if (nodeCardCardRef) nodeCardCardRef.value = el?.$el ?? el ?? null }"
      :node="selectedNode"
      :style="nodeCardStyle"
      :edge-stats="selectedNodeEdgeStats"
      :equivalent-text="state.snapshot?.equivalent ?? ''"
      :show-pin-actions="!isTestMode && !isWebDriver"
      :is-pinned="isSelectedPinned"
      :pin="pinSelectedNode"
      :unpin="unpinSelectedNode"
      :interact-mode="isInteractUi"
      :interact-trustlines="isInteractUi ? interact.mode.trustlines.value : undefined"
      :trustlines-loading="isInteractUi ? interact.mode.trustlinesLoading.value : undefined"
      :interact-busy="isInteractUi ? interact.mode.busy.value : undefined"
      :on-interact-send-payment="isInteractUi ? onInteractSendPayment : undefined"
      :on-interact-new-trustline="isInteractUi ? onInteractNewTrustline : undefined"
      :on-interact-edit-trustline="isInteractUi ? onInteractEditTrustline : undefined"
      @close="setNodeCardOpen(false)"
    />



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
  right: 12px;
  left: auto;
  bottom: 120px;
  padding: 6px 10px;
  pointer-events: none;
}
</style>
