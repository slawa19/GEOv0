<script setup lang="ts">
import TopBar from './TopBar.vue'
import BottomBar from './BottomBar.vue'
import ActionBar from './ActionBar.vue'
import SystemBalanceBar from './SystemBalanceBar.vue'
import ManualPaymentPanel from './ManualPaymentPanel.vue'
import TrustlineManagementPanel from './TrustlineManagementPanel.vue'
import EdgeDetailPopup from './EdgeDetailPopup.vue'
import ClearingPanel from './ClearingPanel.vue'
import EdgeTooltip from './EdgeTooltip.vue'
import LabelsOverlayLayers from './LabelsOverlayLayers.vue'
import NodeCardOverlay from './NodeCardOverlay.vue'
import DevPerfOverlay from './DevPerfOverlay.vue'
import ErrorToast from './ErrorToast.vue'
import InteractHistoryLog from './InteractHistoryLog.vue'

import { computed, isRef, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import type { InteractPhase } from '../composables/useInteractMode'
import { useSimulatorStorage } from '../composables/usePersistedSimulatorPrefs'

// TD-1: all localStorage access is delegated to this composable.
const simulatorStorage = useSimulatorStorage()

type UiThemeId = 'hud' | 'shadcn' | 'saas' | 'library'

function normalizeThemeId(v: unknown): UiThemeId {
  const s = String(v ?? '').trim().toLowerCase()
  if (s === 'shadcn') return 'shadcn'
  if (s === 'saas') return 'saas'
  if (s === 'library') return 'library'
  return 'hud'
}

function readThemeFromUrl(): UiThemeId {
  try {
    return normalizeThemeId(new URLSearchParams(window.location.search).get('theme'))
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
  const theme = normalizeThemeId(next)
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
})

onUnmounted(() => {
  window.removeEventListener('popstate', syncThemeFromUrl)
})

import type { GraphLink } from '../types'

import { useSimulatorApp } from '../composables/useSimulatorApp'
import { emptyToNull } from '../utils/valueFormat'
import { handleEscOverlayStack } from '../utils/escOverlayStack'

const app = useSimulatorApp()

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
  edgeTooltipStyle,
  selectedNode,
  nodeCardStyle,
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

const interactPhase = computed<InteractPhase>(() => {
  // Real app: `phase` is a Ref. Unit tests may mock it as a plain string.
  const p = interact.mode.phase as any
  return (isRef(p) ? p.value : p) as InteractPhase
})

const activePanelType = computed<'payment' | 'trustline' | 'clearing' | null>(() => {
  const p = String(interactPhase.value ?? '').toLowerCase()
  if (p.includes('payment')) return 'payment'
  if (p.includes('trustline')) return 'trustline'
  if (p.includes('clearing')) return 'clearing'
  return null
})

const isInteractActivePhase = computed(() => {
  if (!isInteractUi.value) return false
  return String(interactPhase.value ?? '').toLowerCase() !== 'idle'
})

const activeSegment = computed(() =>
  apiMode.value !== 'real' ? 'sandbox' : isInteractUi.value ? 'interact' : 'auto'
)

/** True when running in Auto-Run mode (real API, non-interact, non-demo). */
const isAutoRunUi = computed(() => apiMode.value === 'real' && !isInteractUi.value && !isDemoUi.value)

/** True when initial data has loaded (or we already have a snapshot from a previous load). */
const dataReady = computed(() => !state.loading || !!state.snapshot)

function isFormLikeTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null
  const tag = String((el as any)?.tagName ?? '').toLowerCase()
  return tag === 'input' || tag === 'textarea' || tag === 'select'
}

function onGlobalKeydown(ev: KeyboardEvent) {
  handleEscOverlayStack(ev, {
    isNodeCardOpen: () => isNodeCardOpen.value,
    closeNodeCard: () => setNodeCardOpen(false),

    isInteractActive: () => isInteractActivePhase.value,
    cancelInteract: () => interact.mode.cancel(),

    isFormLikeTarget,
    dispatchInteractEsc: () => {
      try {
        const escEvt = new CustomEvent('geo:interact-esc', { cancelable: true })
        return window.dispatchEvent(escEvt)
      } catch {
        return true
      }
    },
  })
}

onMounted(() => {
  if (typeof window === 'undefined') return
  window.addEventListener('keydown', onGlobalKeydown)
})

onUnmounted(() => {
  if (typeof window === 'undefined') return
  window.removeEventListener('keydown', onGlobalKeydown)
})

const interactRunTerminal = computed(() => {
  const st = String(real.runStatus?.state ?? '').toLowerCase()
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
  if (fxDebug.busy.value) return
  real.lastError = ''
  try {
    await fxDebug.runTxOnce()
  } catch (e: any) {
    real.lastError = formatDemoActionError(e)
  }
}

const demoRunClearingOnce = async () => {
  if (fxDebug.busy.value) return
  real.lastError = ''
  try {
    await fxDebug.runClearingOnce()
  } catch (e: any) {
    real.lastError = formatDemoActionError(e)
  }
}

function setQueryAndReload(mut: (sp: URLSearchParams) => void) {
  if (typeof window === 'undefined') return
  const url = new URL(window.location.href)
  mut(url.searchParams)
  // Setting href ensures full re-init (important when switching between pipelines).
  window.location.href = url.toString()
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
  setQueryAndReload((sp) => {
    sp.set('mode', 'real')
    sp.set('ui', 'demo')
    sp.set('debug', '1')
  })
}

const isExiting = ref(false)

const tlPanel = ref<InstanceType<typeof TrustlineManagementPanel> | null>(null)

/** Local anchor for positioning TrustlineManagementPanel near the node
 *  when opened from NodeCardOverlay. Separate from state.edgeAnchor
 *  (which drives EdgeDetailPopup visibility). */
const trustlinePanelAnchor = ref<{ x: number; y: number } | null>(null)

/**
 * Controls which UI to show in `editing-trustline` phase:
 * - `true`  → TrustlineManagementPanel (full editing — from NodeCard ✏️, ActionBar, or "Change Limit")
 * - `false` → EdgeDetailPopup (quick info — from edge click on canvas)
 *
 * For non-editing-trustline phases (picking, confirm-create, etc.),
 * TrustlineManagementPanel is always shown regardless of this flag.
 */
const useFullTrustlineEditor = ref(false)

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
  // Switch from EdgeDetailPopup (quick info) to TrustlineManagementPanel (full editor).
  useFullTrustlineEditor.value = true
  // Focus the limit editor in TrustlineManagementPanel via template ref.
  void nextTick(() => tlPanel.value?.focusNewLimit())
}

function onEdgeDetailCloseLine() {
  // Delegate to mode action (will transition to idle on success).
  if (interactPhase.value !== 'editing-trustline') return
  void interact.mode.confirmTrustlineClose()
}

// BUG-1: NodeCardOverlay interact mode handlers
function onInteractSendPayment(fromPid: string) {
  setNodeCardOpen(false)
  interact.mode.startPaymentFlow()
  interact.mode.setPaymentFromPid(fromPid)
}

function onInteractNewTrustline(fromPid: string) {
  setNodeCardOpen(false)
  interact.mode.startTrustlineFlow()
  interact.mode.setTrustlineFromPid(fromPid)
}

function onInteractEditTrustline(fromPid: string, toPid: string) {
  // Capture the NodeCard's screen position as anchor before closing it,
  // so the TrustlineManagementPanel opens near the node instead of
  // jumping to the fixed top-right corner.
  const style = nodeCardStyle.value
  let anchor: { x: number; y: number } | null = null
  if (style.left && style.top) {
    const x = parseInt(style.left as string, 10)
    const y = parseInt(style.top as string, 10)
    if (Number.isFinite(x) && Number.isFinite(y)) {
      anchor = { x, y }
    }
  }

  useFullTrustlineEditor.value = true
  setNodeCardOpen(false)
  interact.mode.selectTrustline(fromPid, toPid)

  // Set anchor AFTER the phase change so the interactPhase watcher
  // (which clears the anchor on every transition) has already fired.
  if (anchor) {
    void nextTick(() => {
      trustlinePanelAnchor.value = anchor
    })
  }
}

function onActionStartPaymentFlow() {
  setNodeCardOpen(false)
  trustlinePanelAnchor.value = null
  interact.mode.startPaymentFlow()
}

function onActionStartTrustlineFlow() {
  setNodeCardOpen(false)
  trustlinePanelAnchor.value = null
  useFullTrustlineEditor.value = true
  interact.mode.startTrustlineFlow()
}

function onActionStartClearingFlow() {
  setNodeCardOpen(false)
  trustlinePanelAnchor.value = null
  interact.mode.startClearingFlow()
}

// Mutual exclusion: when an interact panel activates, close NodeCard.
// When leaving trustline phase, clear the position anchor.
watch(activePanelType, (t, prev) => {
  // Any interact panel opens → close NodeCard to avoid overlapping windows.
  if (t !== null && t !== prev) {
    setNodeCardOpen(false)
  }
  // Leaving trustline phase → clear anchor.
  if (t !== 'trustline') {
    trustlinePanelAnchor.value = null
  }
})

// When phase changes, reset local UI flags.
// - Clear anchor (only set explicitly by onInteractEditTrustline)
// - Reset useFullTrustlineEditor when leaving trustline phases (idle, payment, clearing)
//   so the NEXT edge click shows EdgeDetailPopup (not full editor).
//   NOTE: do NOT reset when transitioning between trustline sub-phases
//   (e.g. picking-trustline-to → editing-trustline) to preserve the ActionBar intent.
watch(interactPhase, (phase) => {
  trustlinePanelAnchor.value = null

  const p = String(phase ?? '').toLowerCase()
  const isTrustlinePhase = p.includes('trustline')
  if (!isTrustlinePhase) {
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
      :style="edgeTooltipStyle()"
      :get-node-name="(id) => getNodeById(id)?.name ?? null"
      :interact-mode="isInteractUi"
    />

    <TopBar
      :api-mode="apiMode"
      :active-segment="activeSegment"
      :is-interact-ui="isInteractUi"
      :is-test-mode="isTestMode"
      :ui-theme="uiTheme"
      :set-ui-theme="setUiTheme"
      :loading-scenarios="real.loadingScenarios"
      :scenarios="real.scenarios"
      :selected-scenario-id="real.selectedScenarioId"
      :desired-mode="real.desiredMode"
      :intensity-percent="real.intensityPercent"
      :run-id="real.runId"
      :run-status="real.runStatus"
      :sse-state="real.sseState"
      :last-error="real.lastError"
      :refresh-scenarios="realActions.refreshScenarios"
      :start-run="realActions.startRun"
      :pause="realActions.pause"
      :resume="realActions.resume"
      :stop="realActions.stop"
      :apply-intensity="realActions.applyIntensity"
      :run-stats="real.runStats"
      :go-sandbox="goSandbox"
      :go-auto-run="goAutoRun"
      :go-interact="goInteract"
      :access-token="real.accessToken"
      :admin-runs="admin.runs.value"
      :admin-runs-loading="admin.loading.value"
      :admin-last-error="admin.lastError.value"
      :admin-get-runs="admin.getRuns"
      :admin-stop-runs="admin.stopRuns"
      :admin-attach-run="admin.attachRun"
      :admin-stop-run="admin.stopRun"
      @update:selected-scenario-id="realActions.setSelectedScenarioId"
      @update:desired-mode="realActions.setDesiredMode"
      @update:intensity-percent="realActions.setIntensityPercent"
    />

    <SystemBalanceBar
      v-if="dataReady && (isInteractUi || isAutoRunUi)"
      :balance="interact.systemBalance"
      :equivalent="effectiveEq"
      :compact="isAutoRunUi"
    />

    <ActionBar
      v-if="dataReady && apiMode === 'real' && isInteractUi"
      :phase="interactPhase"
      :busy="interact.mode.busy.value"
      :actions-disabled="interact.actions.actionsDisabled.value"
      :run-terminal="interactRunTerminal"
      :start-payment-flow="onActionStartPaymentFlow"
      :start-trustline-flow="onActionStartTrustlineFlow"
      :start-clearing-flow="onActionStartClearingFlow"
    />

    <Transition name="panel-slide" mode="out-in">
      <ManualPaymentPanel
        v-if="apiMode === 'real' && isInteractUi && activePanelType === 'payment'"
        key="payment"
        :phase="interactPhase"
        :state="interact.mode.state"
        :unit="effectiveEq"
        :available-capacity="interact.mode.availableCapacity.value"
        :participants="interact.mode.participants.value"
        :busy="interact.mode.busy.value"
        :can-send-payment="interact.mode.canSendPayment.value"
        :confirm-payment="interact.mode.confirmPayment"
        :set-from-pid="interact.mode.setPaymentFromPid"
        :set-to-pid="interact.mode.setPaymentToPid"
        :cancel="interact.mode.cancel"
      />

      <TrustlineManagementPanel
        v-else-if="apiMode === 'real' && isInteractUi && showTrustlinePanel"
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
        :edge-anchor="trustlinePanelAnchor"
        :host-el="hostEl"
      />

      <ClearingPanel
        v-else-if="apiMode === 'real' && isInteractUi && activePanelType === 'clearing'"
        key="clearing"
        :phase="interactPhase"
        :state="interact.mode.state"
        :busy="interact.mode.busy.value"
        :equivalent="effectiveEq"
        :confirm-clearing="interact.mode.confirmClearing"
        :cancel="interact.mode.cancel"
      />
    </Transition>

    <EdgeDetailPopup
      v-if="apiMode === 'real' && isInteractUi"
      :phase="interactPhase"
      :state="interact.mode.state"
      :host-el="hostEl"
      :unit="effectiveEq"
      :used="emptyToNull(interactSelectedLink?.used)"
      :limit="emptyToNull(interactSelectedLink?.trust_limit)"
      :available="emptyToNull(interactSelectedLink?.available)"
      :status="(emptyToNull(interactSelectedLink?.status) as any) ?? null"
      :busy="interact.mode.busy.value"
      :force-hidden="useFullTrustlineEditor"
      :close="interact.mode.cancel"
      @change-limit="onEdgeDetailChangeLimit"
      @close-line="onEdgeDetailCloseLine"
    />

    <NodeCardOverlay
      v-if="isNodeCardOpen && selectedNode && !dragToPin.dragState.active"
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
    <ErrorToast
      v-if="isInteractUi"
      :message="interact.mode.state.error"
      @dismiss="interact.mode.state.error = null"
    />

    <!-- BUG-5: Interact Mode inline history log. -->
    <div
      v-if="isInteractUi && interact.mode.history.length > 0"
      class="ds-ov-bottom"
      style="right: 12px; left: auto; bottom: 120px; padding: 6px 10px; pointer-events: none"
    >
      <InteractHistoryLog :entries="interact.mode.history" :max-visible="8" />
    </div>
  </div>
</template>

<style scoped>
</style>
