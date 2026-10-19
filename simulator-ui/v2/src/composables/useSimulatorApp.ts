/**
 * Facade / stable entry point for the simulator app composable layer.
 *
 * Why it exists:
 * - the app wiring is implemented as a set of focused `useApp*` composables (view wiring, canvas interactions,
 *   double-click handlers, etc.) and their internal structure may evolve;
 * - this file provides a single, stable import path for developers and documentation.
 *
 * Important: this is intentionally a thin wiring entry point — avoid changing behavior here unless necessary.
 */

import { computed, onMounted, onUnmounted, reactive, ref, shallowRef, watch, type ComputedRef, type Ref } from 'vue'
import { fixturesBase, loadJson, loadSnapshot as loadSnapshotFixtures, loadSnapshotAt as loadSnapshotFixturesAt } from '../fixtures'
import { computeLayoutForMode, type LayoutMode } from '../layout/forceLayout'
import { fillForNode } from '../render/nodeFill'
import { sizeForNode } from '../render/nodeSizing'
import type { ClearingDoneEvent, GraphSnapshot } from '../types'
import type { LabelsLod, Quality } from '../types/uiPrefs'
import { classifyNode, createVizMapping, resolveVizColors as resolveVizColorsInto, type DataPalette } from '../vizMapping'
import type { SimulatorAppState } from '../types/simulatorApp'
import { keyEdge } from '../utils/edgeKey'
import { fnv1a } from '../utils/hash'
import { resolveTxDirection } from '../utils/txDirection'
import { incCounter } from '../utils/counters'
import { extractErrorMessage } from '../utils/errorMessage'
import { isLiveRunState } from '../utils/status'
import { toLower, toLowerTrim } from '../utils/stringHelpers'
import { isUserFacingRunErrorCode } from '../utils/runErrorClassification'
import { createPatchApplier } from '../demo/patches'
import { resetFxRendererCaches, spawnEdgePulses, spawnNodeBursts, spawnSparks } from '../render/fxRenderer'
import { getLinkTermination } from '../render/linkGeometry'
import { resetGlowSpritesCache } from '../render/glowSprites'

import { getSnapshot, getScenarioPreview } from '../api/simulatorApi'
import { ApiError, isTimeoutError } from '../api/http'
import type { ArtifactIndexItem, RunStatus, ScenarioSummary } from '../api/simulatorTypes'
import { normalizeApiBase } from '../api/apiBase'
import type { Point } from '../types/layout'

import { useAppLifecycle } from './useAppLifecycle'
import { useAppUiDerivedState } from './useAppUiDerivedState'
import { useLabelNodes } from './useLabelNodes'
import { useLayoutIndex } from './useLayoutIndex'
import { syncLayoutFromSnapshot as syncLayoutFromSnapshotImpl } from './syncLayoutFromSnapshot'
import { usePersistedSimulatorPrefs } from './usePersistedSimulatorPrefs'
import { useMetricsPolling } from './useMetricsPolling'
import { useSelectedNodeEdgeStats } from './useSelectedNodeEdgeStats'
import { useSnapshotIndex } from './useSnapshotIndex'
import { useAppPickingAndHover } from './useAppPickingAndHover'
import { useAppFxOverlays } from './useAppFxOverlays'
import { useAppRenderLoop } from './useAppRenderLoop'
import { useAppPhysicsAndPinningWiring } from './useAppPhysicsAndPinningWiring'
import { useAppLayoutWiring } from './useAppLayoutWiring'
import { useAppDragToPinAndPreview } from './useAppDragToPinAndPreview'
import { useAppCanvasInteractionsWiring } from './useAppCanvasInteractionsWiring'
import { useAppViewWiring } from './useAppViewWiring'
import { useSimulatorRealMode, type RealModeState } from './useSimulatorRealMode'
import { useAppSceneState } from './useAppSceneState'
import { useGeoSimDevHookSetup } from './useGeoSimDevHookSetup'
import { intensityScale } from '../config/fxConfig'
import { EQUIVALENT_CODE_RE, canonicalEquivalent, equivalentOptions as listEquivalentOptions, sameEquivalent } from '../config/equivalents'
import { catalogueEquivalentCodes } from '../config/equivalentPrecision'
import { createSimulatorIsAnimating } from './simulatorIsAnimating'

import { useQualityAutoguards } from './useQualityAutoguards'
import { useCookieSessionBootstrap } from './useCookieSessionBootstrap'
import { defaultIntensityPercentFor, useInteractAutoBootstrapRun } from './useInteractAutoBootstrapRun'

import { useInteractActions } from './useInteractActions'
import { useInteractMode } from './useInteractMode'
import { participantDisplayIdFor, shownScenarioIdOf } from './useParticipantDisplay'
import { useSystemBalance } from './useSystemBalance'

import { CLEARING_EFFECT_MS, useRealClearingFx, type ClearingFxParams } from './realFx/useRealClearingFx'
import { useRealTxFx } from './realFx/useRealTxFx'
import {
  createLivePathDelivery,
  createRecordingSceneSource,
  RECORDING_SCENE_SETTLE_MAX_MS,
  usePlaylistWorkbench,
  type PlaylistWorkbench,
} from './usePlaylistWorkbench'

export type OutsideClickOverlayKey = 'edge-detail' | 'node-card'

/** The `sourcePath` of a run's snapshot: the identity of that scene source, also for a load that has not answered. */
function runSnapshotSourcePath(apiBase: string, runId: string, equivalent: string): string {
  return `GET ${apiBase}/simulator/runs/${encodeURIComponent(runId)}/graph/snapshot?equivalent=${encodeURIComponent(equivalent)}`
}

export async function loadStrictRunRecoverySnapshot(input: {
  apiBase: string
  accessToken: string
  runId: string
  equivalent: string
}): Promise<{ snapshot: GraphSnapshot; sourcePath: string }> {
  const runId = String(input.runId || '').trim()
  const equivalent = canonicalEquivalent(input.equivalent)
  if (!runId || !equivalent) throw new Error('Replay recovery requires a run id and equivalent')

  // Recovery after an unreplayable cursor must never fall back to a scenario
  // preview or an empty graph: only this run-scoped snapshot can cover the gap.
  const snapshot = await getSnapshot(
    { apiBase: input.apiBase, accessToken: input.accessToken },
    runId,
    equivalent,
  )
  if (!sameEquivalent(snapshot.equivalent, equivalent)) {
    throw new Error('Replay recovery snapshot equivalent does not match the requested context')
  }
  return {
    snapshot,
    sourcePath: runSnapshotSourcePath(input.apiBase, runId, equivalent),
  }
}

/**
 * The snapshot of an ACTIVE run for the scene, or `null` when the caller should fall back to the scenario preview.
 * A timeout is THROWN instead (034 S5a, review round 1, item 4); every other failure falls back, and a 404 first
 * resets the stale run.
 *
 * KNOWN DEBT (034 S8, F-034-D2, second half - open): the fallback on a 5xx or a network error shows the scenario
 * preview in place of a live run, unmarked. Throwing it (as the preview's own failure now is) was tried and taken
 * back: nothing re-reads the snapshot of a live run, so one transient failure left the error over a working run
 * for good. Fixing this needs a retry with its own clock, not an edit here.
 */
export async function loadActiveRunSnapshot(input: {
  apiBase: string
  accessToken: string
  runId: string
  equivalent: string
  onStaleRun: () => void
}): Promise<{ snapshot: GraphSnapshot; sourcePath: string } | null> {
  try {
    const snapshot = await getSnapshot({ apiBase: input.apiBase, accessToken: input.accessToken }, input.runId, input.equivalent)
    // Real snapshots currently don't declare FX limits; apply a sane default cap so long sessions
    // can't accumulate too many particles (sparks + pulses + bursts).
    if (!snapshot.limits) snapshot.limits = { max_particles: 220 }
    return {
      snapshot,
      sourcePath: runSnapshotSourcePath(input.apiBase, input.runId, input.equivalent),
    }
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      // Stale runId (e.g. deleted/expired). Clear it so we don't keep calling
      // run-scoped endpoints like /runs/:id/actions/* and /runs/:id.
      input.onStaleRun()
    }
    // A timeout is not "unreachable": the run is alive and its scene is on screen. Swapping that scene for the
    // scenario preview would show a different graph as if it were the run; let the caller report the error and
    // keep what is shown. Every other failure keeps falling back (see KNOWN DEBT above).
    if (isTimeoutError(e)) throw e
    console.warn('Failed to load run snapshot; falling back to scenario preview:', e)
    return null
  }
}

type RealModeApi = ReturnType<typeof useSimulatorRealMode>
type CookieSessionApi = ReturnType<typeof useCookieSessionBootstrap>
type InteractActionsApi = ReturnType<typeof useInteractActions>
type InteractModeApi = ReturnType<typeof useInteractMode>
type UiDerivedApi = ReturnType<typeof useAppUiDerivedState>
type LabelNodesApi = ReturnType<typeof useLabelNodes>
type PickingAndHoverApi = ReturnType<typeof useAppPickingAndHover>
type FxOverlaysApi = ReturnType<typeof useAppFxOverlays>
type ViewWiringApi = ReturnType<typeof useAppViewWiring>
type MetricsPollingApi = ReturnType<typeof useMetricsPolling>
type DragToPinApi = ReturnType<typeof useAppDragToPinAndPreview>
type PhysicsAndPinningApi = ReturnType<typeof useAppPhysicsAndPinningWiring>
type CanvasInteractionsApi = ReturnType<typeof useAppCanvasInteractionsWiring>

export type SimulatorAppApi = {
  apiMode: ComputedRef<'fixtures' | 'real'>

  isDemoFixtures: ComputedRef<boolean>
  /**
   * 043 S2: the ONE screen - the network lives and the protocol actions are at hand on the same page. True for the
   * server source; there is no `?ui=interact` / Auto-Run split any more, and no Demo UI (`?ui=demo`, removed by S3).
   */
  isLiveUi: ComputedRef<boolean>
  /**
   * 048 S1 (decision 5): `?debug=1` - the developer's entry. Only with it are the source (server / offline snapshot), the
   * render quality, the SSE badge and the offline effects workbench (playlist) on screen; a visitor's "Подробнее" has
   * none of them. Read once per page: nothing writes the parameter but a navigation (the source switch carries it).
   */
  isDebugUi: boolean
  isTestMode: ComputedRef<boolean>
  isWebDriver: boolean
  isE2eScreenshots: ComputedRef<boolean>

  real: RealModeState
  realActions: {
    setApiBase: (v: string) => void
    setAccessToken: (v: string) => void
    setSelectedScenarioId: (v: string) => void
    setIntensityPercent: (v: number) => void
  } & Pick<
    RealModeApi,
    | 'startRun'
    | 'pause'
    | 'pauseForManualAction'
    | 'resume'
    | 'stop'
    | 'applyIntensity'
    | 'refreshSnapshot'
  >

  session: Pick<CookieSessionApi, 'actorKind' | 'ownerId' | 'bootstrapping' | 'tryEnsure'>

  /**
   * 048 S2 (amendment 9): "Admin ▾" is gone from the UI - the run list, Stop and Stop all are the API's
   * (`real-mode-runbook.md`, "где и что смотреть"). What is left is the attach to a run by its id: no control of the UI
   * calls it since S2; the composable tests enter the attach of `useSimulatorRealMode.ts` through it (the rules of the
   * shown run's scene that start and boot share). Whether that mechanism goes too is a cleanup of its own (BACKLOG 048-3).
   */
  admin: {
    attachRun: (runId: string) => Promise<void>
  }

  state: SimulatorAppState
  eq: Ref<string>
  layoutMode: Ref<LayoutMode>
  quality: Ref<Quality>
  labelsLod: Ref<LabelsLod>

  effectiveEq: ComputedRef<string>
  /** 028 F-028-47: the equivalent of the snapshot on screen (its numbers, units and actions). */
  sceneEq: ComputedRef<string>
  /** 028 F-028-47 (C1): the EQ selector's codes, from the API. */
  equivalentOptions: ComputedRef<string[]>

  isInteractPickingPhase: ComputedRef<boolean>
  isInteractCanvasNodePickPhase: ComputedRef<boolean>

  interact: {
    actions: InteractActionsApi
    mode: InteractModeApi
    systemBalance: ReturnType<typeof useSystemBalance>['balance']
  }

  gpuAccelLikely: ReturnType<typeof useQualityAutoguards>['gpuAccelLikely']

  /** 039 slice B: the resolved data colours - the object the renderer paints with. For the legend and the node card. */
  dataPalette: Readonly<Ref<DataPalette>>
  /** Read the colour tokens again at the app's root and draw one frame. On mount and after a theme change. */
  resolveVizColors: () => void

  hostEl: Ref<HTMLElement | null>
  canvasEl: Ref<HTMLCanvasElement | null>
  fxCanvasEl: Ref<HTMLCanvasElement | null>
  dragPreviewEl: Ref<HTMLElement | null>

  overlayLabelScale: UiDerivedApi['overlayLabelScale']
  showResetView: UiDerivedApi['showResetView']

  showPerfOverlay: ComputedRef<boolean>
  perf: {
    lastFps: number | null
    fxBudgetScale: number | null
    maxParticles: number | null
    renderQuality: string | null
    dprClamp: number | null
    canvasCssW: number | null
    canvasCssH: number | null
    canvasPxW: number | null
    canvasPxH: number | null
    canvasDpr: number | null
  }

  e2e: {
    runTxOnce: () => Promise<void>
    runClearingOnce: () => Promise<void>
  }

  /** 042 T4205: the offline effects workbench (recorded playlists, offline source only; empty on the server source). */
  playlistWorkbench: PlaylistWorkbench

  hasNodeCardInspectorOpen: ComputedRef<boolean>
  hoveredEdge: FxOverlaysApi['hoveredEdge']
  clearHoveredEdge: FxOverlaysApi['clearHoveredEdge']
  /**
   * The active-edge highlight sink. Exposed because a caller outside the FX composables needs to
   * name one edge on the graph — a bottleneck row saying "this one, not its neighbour" — and this
   * overlay is already the app's single way of saying that.
   */
  addActiveEdge: FxOverlaysApi['addActiveEdge']
  edgeTooltipStyle: PickingAndHoverApi['edgeTooltipStyle']
  selectedNode: ViewWiringApi['selectedNode']
  selectedNodeScreenCenter: ViewWiringApi['selectedNodeScreenCenter']
  getNodeScreenCenter: (nodeId: string) => Point | null
  selectedNodeEdgeStats: ReturnType<typeof useSelectedNodeEdgeStats>['selectedNodeEdgeStats']

  dragToPin: DragToPinApi['dragToPin']
  isSelectedPinned: PhysicsAndPinningApi['isSelectedPinned']
  isNodePinned: PhysicsAndPinningApi['isPinned']
  pinNode: PhysicsAndPinningApi['pinNode']
  unpinNode: PhysicsAndPinningApi['unpinNode']
  pinSelectedNode: PhysicsAndPinningApi['pinSelectedNode']
  unpinSelectedNode: PhysicsAndPinningApi['unpinSelectedNode']

  onCanvasClick: CanvasInteractionsApi['onCanvasClick']
  onCanvasDblClick: CanvasInteractionsApi['onCanvasDblClick']
  onCanvasPointerDown: CanvasInteractionsApi['onCanvasPointerDown']
  onCanvasPointerMove: CanvasInteractionsApi['onCanvasPointerMove']
  onCanvasPointerUp: CanvasInteractionsApi['onCanvasPointerUp']
  onCanvasWheel: CanvasInteractionsApi['onCanvasWheel']

  wakeUp: () => void

  labelNodes: LabelNodesApi['labelNodes']
  floatingLabelsViewFx: FxOverlaysApi['floatingLabelsViewFx']
  worldToCssTranslateNoScale: ViewWiringApi['worldToCssTranslateNoScale']

  getNodeById: ReturnType<typeof useSnapshotIndex>['getNodeById']
  focusOnEdge: ViewWiringApi['focusOnEdge']
  resetView: ViewWiringApi['resetView']

  /**
   * Analytics overlay surface (spec 007): visibility plus the two streams feeding the panel.
   *
   * Two of everything, on purpose. `/metrics` and `/bottlenecks` fail independently, so a single
   * merged phase (or a single shared error string) would make the panel state the absence of data
   * it is holding. The merge does not exist anywhere on this path.
   */
  analytics: {
    isVisible: ComputedRef<boolean>
    metricsPhase: MetricsPollingApi['metricsPhase']
    bottlenecksPhase: MetricsPollingApi['bottlenecksPhase']
    metrics: MetricsPollingApi['metrics']
    bottlenecks: MetricsPollingApi['bottlenecks']
    metricsError: MetricsPollingApi['metricsError']
    bottlenecksError: MetricsPollingApi['bottlenecksError']
    metricsUnavailableReason: MetricsPollingApi['metricsUnavailableReason']
    bottlenecksUnavailableReason: MetricsPollingApi['bottlenecksUnavailableReason']
    isPolling: MetricsPollingApi['isPolling']
  }
}

/**
 * The rule that decides whether the analytics surface exists at all (spec 007, T705).
 *
 * Exported as a named policy for one reason: the only test that mounts `SimulatorAppRoot` mocks
 * this whole composable away, so the mock used to re-state the rule in its own words — and a test
 * that re-states the rule verifies the restatement, not the application. The mock now calls this,
 * which makes "never over fixtures" a fact about the shipped rule.
 *
 * Fixtures have no metric store behind them: a panel opened over a demo scene could only ever say
 * "no measurements", while polling two endpoints that cannot answer for it.
 */
export function __analyticsPanelVisibilityPolicy(realMode: boolean, panelOpen: boolean): boolean {
  return realMode && panelOpen
}

/**
 * Pure policy: decides which overlay is topmost and closeable on outside click.
 *
 * Order is important and matches Step 0 spec:
 *  1) edge-detail
 *  2) node-card
 */
export function __closeTopmostOverlayOnOutsideClickPolicy(opts: {
  edgeDetail: {
    open: boolean
    closeOnOutsideClick: boolean
    close: () => void
  }
  nodeCard: {
    open: boolean
    closeOnOutsideClick: boolean
    close: () => void
  }
}): OutsideClickOverlayKey | null {
  if (opts.edgeDetail.open && opts.edgeDetail.closeOnOutsideClick) {
    opts.edgeDetail.close()
    return 'edge-detail'
  }

  if (opts.nodeCard.open && opts.nodeCard.closeOnOutsideClick) {
    opts.nodeCard.close()
    return 'node-card'
  }

  return null
}

/**
 * Step 0: empty click cancels interact flows.
 * It also closes the topmost closeable overlay (policy-driven).
 */
export function __selectNodeFromCanvasStep0(opts: {
  id: string | null

  isInteractCanvasNodePickPhase: boolean
  interactSelectNode: (nodeId: string) => void

  /** P0-2: when true, hard dismiss (ESC/outside-click) must be confirmed. */
  isInteractBusy?: boolean
  /** P0-2: confirm callback used only when busy and user attempts a hard dismiss. */
  confirmCancelInteractBusy?: () => boolean

  closeTopmostOverlayOnOutsideClick: () => void

  // Explicitly present for regression-guard tests: Step 0 MUST cancel flows on empty click.
  cancelInteract: () => void

  selectNode: (id: string | null) => void
}): void {
  // In Interact UI picking phases: treat node click as a flow input.
  if (opts.id && opts.isInteractCanvasNodePickPhase) {
    opts.interactSelectNode(opts.id)
    return
  }

  // Outside click: cancel active interact flow and close inspector card overlays (edge-detail + node-card).
  if (!opts.id) {
    // P0-2 policy B: when Interact is busy, outside-click hard dismiss must be gated.
    if (opts.isInteractBusy && typeof opts.confirmCancelInteractBusy === 'function') {
      const ok = opts.confirmCancelInteractBusy()
      if (!ok) return
    }

    opts.cancelInteract()

    // Close twice to ensure both cards are dismissed when they co-exist (e.g. during transitions).
    opts.closeTopmostOverlayOnOutsideClick()
    opts.closeTopmostOverlayOnOutsideClick()
    opts.selectNode(null)
    return
  }

  // Default: regular selection for NodeCard/hover.
  opts.selectNode(opts.id)
}

export function __autoBootstrapMaybeFillUiError(opts: {
  real: { lastError?: string | null }
  state: { error?: string | null }
  err: unknown
}): void {
  const hasRealError = !!String(opts.real.lastError ?? '').trim()
  const hasStateError = !!String(opts.state.error ?? '').trim()
  if (hasRealError || hasStateError) return

  opts.state.error = `Auto-start failed: ${extractErrorMessage(opts.err)}`
}

export function useSimulatorApp(opts?: {
  /**
   * Step 5 (WM): close topmost inspector window (edge-detail → node-card) on outside click.
   *
   * When provided, `closeTopmostOverlayOnOutsideClick()` delegates to this callback
   * and MUST NOT mutate legacy overlay state (edgeAnchor / nodeCardOpen).
   */
  uiCloseTopmostInspectorWindow?: () => OutsideClickOverlayKey | null

  /** Step 5 (WM): open/update edge-detail inspector window from edge click. */
  uiOpenOrUpdateEdgeDetail?: (o: { fromPid: string; toPid: string; anchor: { x: number; y: number } }) => void

  /** Step 5 (WM): open/update node-card inspector window from node click/dblclick. */
  uiOpenOrUpdateNodeCard?: (o: { nodeId: string; anchor: { x: number; y: number } | null }) => void

  /** Step 5 (WM): whether node-card inspector window is currently open (WM source of truth). */
  uiIsNodeCardOpen?: () => boolean

  /** Policy seam for non-essential animation and canvas effects. */
  optionalFxEnabled?: () => boolean

  /**
   * T705: has the user asked for the analytics overlay surface?
   *
   * The mount point owns and persists this flag (it is a UI preference, stored the same way as
   * its bottom-bar neighbours); the real-mode half of the decision is added here, and the result
   * is published back as `analytics.isVisible` so the flag and the poll gate cannot disagree.
   *
   * Read reactively — the getter is evaluated inside a `computed`. Omitted means "no such
   * surface": nothing is polled at all.
   */
  isAnalyticsPanelOpen?: () => boolean
}): SimulatorAppApi {
  const eq = ref('UAH')

  const layoutMode = ref<LayoutMode>('admin-force')

  const isDemoFixtures = computed(() => String(import.meta.env.VITE_DEMO_FIXTURES ?? '1') === '1')
  const isTestMode = computed(() => String(import.meta.env.VITE_TEST_MODE ?? '0') === '1')

  // Playwright sets navigator.webdriver=true.
  // Use it to keep screenshot tests stable even if someone runs the dev server with VITE_TEST_MODE=1.
  const isWebDriver = typeof navigator !== 'undefined' && navigator.webdriver === true

  const allowRealModeInWebDriver = computed(() => {
    if (!isTestMode.value) return false
    if (!isWebDriver) return false
    try {
      return new URLSearchParams(window.location.search).get('e2eReal') === '1'
    } catch {
      return false
    }
  })

  const isE2eScreenshots = computed(() => isTestMode.value && isWebDriver && !allowRealModeInWebDriver.value)

  const hasNodeCardInspectorOpen = computed(() => {
    try {
      return typeof opts?.uiIsNodeCardOpen === 'function' ? !!opts.uiIsNodeCardOpen() : false
    } catch {
      return false
    }
  })

  const apiMode = computed<'fixtures' | 'real'>(() => {
    // E2E screenshot tests must be fully offline/deterministic.
    // Force fixtures pipeline regardless of a developer's local `.env.local` (which may enable real mode).
    if (isE2eScreenshots.value) return 'fixtures'
    try {
      const p = toLower(new URLSearchParams(window.location.search).get('mode'))
      if (p === 'real') return 'real'
      if (p === 'fixtures') return 'fixtures'
    } catch {
      // ignore
    }
    const env = toLowerTrim(import.meta.env.VITE_API_MODE)
    if (env === 'real') return 'real'
    if (env === 'fixtures') return 'fixtures'
    // 043 S2 (decision 2): opening the demo is a live run on the server. The offline snapshot is a choice - the
    // "Источник" select under "Подробнее" (`?mode=fixtures`) or `VITE_API_MODE=fixtures` - not the default.
    return 'real'
  })

  const isRealMode = computed(() => apiMode.value === 'real')

  // 048 S1 (decision 5): the one reader of `?debug=1` (see `SimulatorAppApi.isDebugUi`). Exactly `1`, like `?perf=1`.
  const isDebugUi = (() => {
    try {
      return new URLSearchParams(window.location.search).get('debug') === '1'
    } catch {
      return false
    }
  })()

  const isLocalhost = (() => {
    try {
      const host = String(typeof window !== 'undefined' ? window.location.hostname : '')
      return ['localhost', '127.0.0.1', '::1'].includes(host)
    } catch {
      return false
    }
  })()

  // 043 S2 (decision 2): one screen instead of `?ui=interact` (Interact) and its absence (Auto-Run) - a live network with
  // the protocol actions at hand, on the server source. `?ui=interact` and (043 S3) `?ui=demo` in an old link are
  // ignored: the same screen opens.
  const isLiveUi = computed(() => isRealMode.value)

  const DEFAULT_REAL_SCENARIO_ID = 'greenfield-village-100-realistic-v2'

  function lsGet(key: string, fallback = ''): string {
    try {
      const v = localStorage.getItem(key)
      return v == null ? fallback : v
    } catch {
      return fallback
    }
  }

  function lsSet(key: string, value: string) {
    try {
      localStorage.setItem(key, value)
    } catch {
      // ignore
    }
  }

  function lsRemove(key: string) {
    try {
      localStorage.removeItem(key)
    } catch {
      // ignore
    }
  }

  // 042 S2: the run mode is no longer a client setting. A person who once picked "sandbox (topology-only)" in the
  // removed Advanced > Pipeline menu has `fixtures` stored under this key; it is dropped on load (this key only), so
  // it can never again reach a run or preview request - there is no reader left, and the request says `real` itself.
  lsRemove('geo.sim.v2.desiredMode')

  const state = reactive<SimulatorAppState>({
    loading: true,
    error: '',
    sourcePath: '',
    snapshot: null,
    selectedNodeId: null,
  })

  const DEFAULT_DEV_ACCESS_TOKEN = String(import.meta.env.VITE_GEO_DEV_ACCESS_TOKEN ?? 'dev-admin-token-change-me').trim()
  const ENV_ACCESS_TOKEN = String(import.meta.env.VITE_GEO_ACCESS_TOKEN ?? '').trim()

  const initialAccessToken = lsGet(
    'geo.sim.v2.accessToken',
    ENV_ACCESS_TOKEN || (import.meta.env.DEV ? DEFAULT_DEV_ACCESS_TOKEN : ''),
  )
  const real = reactive<RealModeState>({
    apiBase: normalizeApiBase(lsGet('geo.sim.v2.apiBase', String(import.meta.env.VITE_GEO_API_BASE ?? '/api/v1'))),
    accessToken: initialAccessToken,
    loadingScenarios: false,
    scenarios: [] as ScenarioSummary[],
    selectedScenarioId: lsGet('geo.sim.v2.selectedScenarioId', ''),
    // Default lower than demo to keep real-mode readable (avoid dozens of overlapping sparks).
    intensityPercent: (() => {
      // 044 stage 2: the visitor's tempo (15) on a screen without an admin token, the operator's (30) with one.
      const fallback = defaultIntensityPercentFor(initialAccessToken)
      const raw = lsGet('geo.sim.v2.intensityPercent', '')
      // 043 S3 (decision 5): a stored 0 is what the removed Interact mode forced and kept; it is dropped here, once per
      // stored value (the key is removed), instead of a reset after the pipeline is wired. A 0 a person stores by hand is
      // dropped the same way at the next load - nothing tells the two apart - as the removed reset did on every load.
      if (raw.trim() === '0') {
        try {
          localStorage.removeItem('geo.sim.v2.intensityPercent')
        } catch {
          // ignore
        }
        return fallback
      }
      if (!raw) return fallback
      const n = Number(raw)
      if (!Number.isFinite(n)) return fallback
      return Math.max(0, Math.min(100, Math.round(n)))
    })(),
    runId: lsGet('geo.sim.v2.runId', '') || null,
    runStatus: null as RunStatus | null,
    sseState: 'idle',
    lastEventId: null as string | null,
    lastError: '',
    lastErrorLine: null,
    retryableStartLine: null,
    artifacts: [] as ArtifactIndexItem[],
    artifactsLoading: false,

    // Live run stats (ephemeral; not persisted).
    runStats: {
      startedAtMs: 0,
      attempts: 0,
      committed: 0,
      rejected: 0,
      errors: 0,
      timeouts: 0,
      rejectedByCode: {} as Record<string, number>,
      errorsByCode: {} as Record<string, number>,
    },
  })

  /**
   * The scenario of the scene on screen: the shown run's, else the selected one. It is read ONLY to remove the scenario
   * namespace from participant ids that are printed to a person (`utils/participantDisplayId.ts`).
   */
  const shownScenarioId = computed(() => shownScenarioIdOf(real.runStatus, real.selectedScenarioId))

  function resetRunStats() {
    real.runStats.startedAtMs = Date.now()
    real.runStats.attempts = 0
    real.runStats.committed = 0
    real.runStats.rejected = 0
    real.runStats.errors = 0
    real.runStats.timeouts = 0
    real.runStats.rejectedByCode = {}
    real.runStats.errorsByCode = {}
  }

  const inc = incCounter

  // ============================
  // §10: Cookie session state (anonymous visitors)
  // ============================

  const session = useCookieSessionBootstrap({
    isRealMode,
    apiBase: computed(() => real.apiBase),
    accessToken: computed(() => real.accessToken),
  })


  // tx.failed often represents a "clean rejection" (routing capacity, trustline constraints).
  // Those should not be surfaced as global "errors" in the HUD.
  //
  // Classification must match backend _emit_if_ready (real_runner.py):
  //   err_code = "PAYMENT_TIMEOUT" | "INTERNAL_ERROR" → errors_total
  //   everything else (map_rejection_code results) → rejected_total
  //
  // Note: backend-authoritative stats arrive in run_status every ~1s and
  // overwrite local optimistic counters, so this is only for the gap.
  function isUserFacingRunError(code: string): boolean {
    return isUserFacingRunErrorCode(code)
  }

  function pickDefaultScenarioId(scenarios: ScenarioSummary[]): string {
    const preferred = scenarios.find((s) => s.scenario_id === DEFAULT_REAL_SCENARIO_ID)?.scenario_id
    return preferred ?? scenarios[0]?.scenario_id ?? ''
  }

  function ensureScenarioSelectionValid() {
    if (!real.scenarios.length) return
    const hasSelected = real.scenarios.some((s) => s.scenario_id === real.selectedScenarioId)
    if (!hasSelected) real.selectedScenarioId = pickDefaultScenarioId(real.scenarios)
  }

  // Dev convenience: keep real mode usable even if localStorage token is empty.
  // Safety: only auto-fill on localhost.
  if (import.meta.env.DEV && isLocalhost && !String(real.accessToken ?? '').trim()) {
    real.accessToken = DEFAULT_DEV_ACCESS_TOKEN
  }

  watch(
    () => real.apiBase,
    (v) => lsSet('geo.sim.v2.apiBase', normalizeApiBase(v)),
  )
  watch(
    () => real.accessToken,
    (v) => lsSet('geo.sim.v2.accessToken', v),
  )
  watch(
    () => real.selectedScenarioId,
    (v) => lsSet('geo.sim.v2.selectedScenarioId', v),
  )
  watch(
    () => real.intensityPercent,
    (v) => lsSet('geo.sim.v2.intensityPercent', String(v)),
  )
  watch(
    () => real.runId,
    (v) => lsSet('geo.sim.v2.runId', v ?? ''),
  )

  function selectNode(id: string | null) {
    state.selectedNodeId = id
  }

  const canvasEl = ref<HTMLCanvasElement | null>(null)
  const fxCanvasEl = ref<HTMLCanvasElement | null>(null)
  const hostEl = ref<HTMLDivElement | null>(null)
  const dragPreviewEl = ref<HTMLDivElement | null>(null)

  async function e2eTxOnce(): Promise<void> {
    if (!isE2eScreenshots.value) return
    // Offline deterministic action for Playwright screenshot tests.
    // Render something visible on the *base* canvas (not FX canvas) by using active edge overlay.
    const snap = state.snapshot
    if (!snap || snap.links.length === 0) return
    const l = snap.links[0]!
    addActiveEdge(keyEdge(l.source, l.target), 1500)
    // Also show an "active" node glow to make the scene change more robust.
    addActiveNode(String(l.source), 1500)
    addActiveNode(String(l.target), 1500)
    wakeUp()
  }

  async function e2eClearingOnce(): Promise<void> {
    if (!isE2eScreenshots.value) return
    // Offline deterministic clearing step.
    // Use active nodes (clearing glow in baseGraph) instead of active edges (tx color policy).
    const snap = state.snapshot
    if (!snap || snap.nodes.length === 0) return
    const ids = snap.nodes.slice(0, 4).map((n) => String(n.id))
    for (const id of ids) addActiveNode(id, 1500)
    wakeUp()
  }

  const snapshotIndex = useSnapshotIndex({
    getSnapshot: () => state.snapshot,
  })

  const getNodeById = snapshotIndex.getNodeById

  /**
   * 039 slice B: the ONE mapping of this app. The renderer holds this object; `dataPalette` is its resolved data
   * colours as the legend and the node card receive them - the same object, so the canvas and the DOM cannot differ.
   * Colours are read from the stylesheet here (before mount: the document root) and again by `resolveVizColors` at
   * the app's real root, on mount and after every theme change. Nothing reads a token per node or per frame.
   */
  const vizMapping = createVizMapping(null)
  const dataPalette = shallowRef<DataPalette>(vizMapping.node.palette)

  function resolveVizColors(): void {
    dataPalette.value = resolveVizColorsInto(vizMapping, hostEl.value)
    // One frame with the new colours: the loop may be asleep, and a theme change moves nothing by itself.
    wakeUp()
  }

  function fxColorForNode(nodeId: string, fallback: string): string {
    const n = getNodeById(nodeId)
    if (!n) return fallback
    return fillForNode(n, vizMapping)
  }

  const labelsLod = ref<LabelsLod>('selection')
  // Baseline default. Persisted prefs (if any) will override this on mount.
  // For Playwright screenshot tests we must use the exact visuals the snapshots were recorded with.
  // (In particular: gradients + full glow sprites are enabled only on `high`.)
  const quality = ref<Quality>(isE2eScreenshots.value ? 'high' : 'med')

  const { gpuAccelLikely } = useQualityAutoguards({
    quality,
    isTestMode,
    isWebDriver,
  })

  // §10: Cookie bootstrap — ensure session for anonymous visitors at startup.
  // Only runs in real mode, and only if no explicit accessToken is configured.
  onMounted(() => {
    if (!isRealMode.value) return
    void session.tryEnsure()
  })

  // uiDerived reads camera.zoom (for overlay label scale). Keep this wiring robust
  // by using a replaceable getter, so no TDZ/lazy-eval footgun is possible.
  let getCameraZoomSafe = () => 1
  const uiDerived = useAppUiDerivedState({
    eq,
    quality,
    apiMode,
    isDemoFixtures,
    isTestMode,
    isWebDriver,
    getCameraZoom: () => getCameraZoomSafe(),
  })

  const effectiveEq = uiDerived.effectiveEq
  // 028 F-028-47 (B6): while a new equivalent loads, or after its load failed, the old snapshot stays on
  // screen - its numbers, units and interact actions are of ITS equivalent, not of the selector's.
  const sceneEq = computed(() => canonicalEquivalent(state.snapshot?.equivalent) || effectiveEq.value)
  const equivalentOptions = computed(() => listEquivalentOptions({
    apiMode: apiMode.value,
    scenario: real.scenarios.find((x) => x.scenario_id === real.selectedScenarioId)?.equivalents,
    catalogue: catalogueEquivalentCodes(),
    current: eq.value,
  }))
  const dprClamp = uiDerived.dprClamp
  const showResetView = uiDerived.showResetView
  const overlayLabelScale = uiDerived.overlayLabelScale

  const snapshotRef = computed(() => state.snapshot)

  // Interact Mode core (state machine + actions + balance).
  // NOTE: this is intentionally lightweight wiring; UI panels/picking integration is done in later phases.
  const interactHttpConfig = computed(() => ({ apiBase: real.apiBase, accessToken: real.accessToken }))
  const interactRunId = computed({
    get: () => String(real.runId ?? ''),
    set: (v: string) => {
      const next = String(v ?? '').trim()
      real.runId = next ? next : null
    },
  })
  let resetStaleRunOwner: ((opts?: { clearError?: boolean }) => void) | null = null
  function resetStaleRunThroughOwner(opts?: { clearError?: boolean }) {
    if (resetStaleRunOwner) {
      resetStaleRunOwner(opts)
      return
    }
    // Real-mode boot is asynchronous, while the owner is wired later in setup.
    // Defer the extremely early completion case instead of duplicating reset policy here.
    queueMicrotask(() => resetStaleRunOwner?.(opts))
  }
  const interactActions = useInteractActions({
    httpConfig: interactHttpConfig,
    runId: interactRunId,
    onStaleRunId: () => {
      resetStaleRunThroughOwner({ clearError: true })
    },
  })

  // Clearing FX is wired later (depends on fxState + overlays), but must be callable
  // from early wiring (e.g. Interact callbacks) without TDZ hazards.
  let runClearingFxImpl: (params: ClearingFxParams, opts?: { animate?: boolean }) => void = () => undefined
  function runClearingFx(params: ClearingFxParams) {
    runClearingFxImpl(params, { animate: opts?.optionalFxEnabled?.() !== false })
  }

  let runRealClearingDoneFxImpl: (done: ClearingDoneEvent, opts?: { animate?: boolean }) => void = () => undefined
  function runRealClearingDoneFx(done: ClearingDoneEvent) {
    runRealClearingDoneFxImpl(done, { animate: opts?.optionalFxEnabled?.() !== false })
  }

  let resetClearingFxDedupImpl: () => void = () => undefined

  const interactMode = useInteractMode({
    showPid: participantDisplayIdFor(() => shownScenarioId.value),
    actions: interactActions,
    runId: interactRunId,
    equivalent: sceneEq,
    snapshot: snapshotRef,
    // Keep node selection highlight in sync with picking-driven flows.
    onNodeClick: (id) => {
      selectNode(id)
    },
    // BUG-3: spawn all clearing FX on clearing cycle edges after successful clearing.
    onClearingDone: (res) => {
      const edges: Array<{ from: string; to: string }> = []
      for (const cycle of res.cycles ?? []) {
        for (const e of cycle.edges ?? []) {
          if (e.from && e.to) edges.push({ from: e.from, to: e.to })
        }
      }
      if (edges.length === 0) return

      runClearingFx({
        edges,
        totalAmount: res.total_cleared_amount ?? '0',
        equivalent: res.equivalent ?? sceneEq.value,
      })
    },
  })
  /**
   * 043 S2 (decision 3; external review 2026-10-17): every EXPLICIT entry or choice of a manual action pauses the run -
   * starting a flow (the action bar, a node card, a line's popup: all call these), picking a participant on the canvas,
   * choosing a sender, recipient, creditor, debtor or line in a panel. Wrapped here, at the one object every caller
   * reaches (the root's panels, the canvas, the window controller), so an entry does not depend on what changed in
   * the state: until this round a watched "some flow is open" boolean missed a second entry made in the same tick, a
   * choice inside an open panel and the full editor of a line. A cleared field (`null`: Escape-back, the window
   * controller) is not an entry. Internal transitions of the flow call the FSM directly and are not wrapped.
   */
  {
    const entries = [
      'startPaymentFlow', 'startPaymentFlowWithFrom', 'startTrustlineFlow', 'startTrustlineFlowWithFrom', 'startClearingFlow',
      'selectNode', 'selectEdge', 'setPaymentFromPid', 'setPaymentToPid', 'setTrustlineFromPid', 'setTrustlineToPid',
      'selectTrustline',
    ] as const
    const target = interactMode as unknown as Record<string, (...a: unknown[]) => unknown>
    for (const name of entries) {
      const inner = target[name]!
      target[name] = (...a: unknown[]) => {
        const out = inner(...a)
        const cleared = a.length === 1 && a[0] === null
        if (!cleared && out !== false) noteManualAction()
        return out
      }
    }
  }
  const systemBalance = useSystemBalance(snapshotRef).balance

  // `fxAndRender` is initialized later (it needs layout + camera), but layout/resize
  // code may want to wake the render loop. IMPORTANT: this must be a stable wrapper.
  // Some wirings capture the function reference at init-time; reassigning a `let wakeUp = ...`
  // later would not update the captured reference.
  let wakeUpImpl: () => void = () => undefined
  const wakeUp = () => wakeUpImpl()


  const layoutWiring = useAppLayoutWiring({
    canvasEl,
    fxCanvasEl,
    hostEl,
    snapshot: snapshotRef,
    layoutMode,
    dprClamp,
    isTestMode,
    getSourcePath: () => state.sourcePath,
    computeLayoutForMode,
    wakeUp,
  })

  const layoutCoordinator = layoutWiring.layoutCoordinator

  const layout = layoutCoordinator.layout
  const resizeAndLayout = layoutCoordinator.resizeAndLayout
  const requestResizeAndLayout = layoutCoordinator.requestResizeAndLayout

  const layoutIndexHelpers = useLayoutIndex({
    getLayoutNodes: () => layout.nodes,
    getLayoutLinks: () => layout.links,
    getSelectedNodeId: () => state.selectedNodeId,
  })

  const layoutIndex = layoutIndexHelpers.layoutIndex
  const layoutLinkMap = layoutIndexHelpers.layoutLinkMap
  const selectedIncidentEdgeKeys = layoutIndexHelpers.selectedIncidentEdgeKeys
  const getLayoutNodeById = layoutIndexHelpers.getLayoutNodeById

  // Layout nodes are raw (non-reactive) for physics performance.
  // Any consumer that uses Vue `computed()` based on node.__x/__y must explicitly depend on
  // layoutCoordinator.layoutVersion to re-evaluate during physics/drag updates.
  const getLayoutNodesLive = () => {
    void layoutCoordinator.layoutVersion.value // dependency read (see above) - the read itself is the effect
    return layout.nodes
  }
  const getLayoutNodeByIdLive = (id: string) => {
    void layoutCoordinator.layoutVersion.value // dependency read (see above) - the read itself is the effect
    return getLayoutNodeById(id)
  }

  function syncLayoutFromSnapshot(snapshot: GraphSnapshot) {
    syncLayoutFromSnapshotImpl(layout, snapshot)
    wakeUp()
  }

  const persistedPrefs = usePersistedSimulatorPrefs({
    layoutMode,
    quality,
    labelsLod,
    requestResizeAndLayout,
    // E2E screenshots must not depend on a developer's localStorage (layout/quality prefs).
    // Otherwise snapshots become machine-specific.
    storage: isE2eScreenshots.value ? { getItem: () => null, setItem: () => undefined } : undefined,
  })

  const viewWiring = useAppViewWiring({
    canvasEl,
    hostEl,
    getLayoutNodes: getLayoutNodesLive,
    getLayoutW: () => layout.w,
    getLayoutH: () => layout.h,
    isTestMode: () => isTestMode.value,
    // Wake up render loop after camera changes are actually applied (wheel batches).
    onCameraChanged: wakeUp,
    setClampCameraPan: (fn) => layoutCoordinator.setClampCameraPan(fn),
    selectedNodeId: computed({
      get: () => state.selectedNodeId,
      set: (v) => {
        state.selectedNodeId = v
      },
    }),
    setSelectedNodeId: (id) => {
      state.selectedNodeId = id
    },
    getNodeById,
    getLayoutNodeById: (id) => getLayoutNodeByIdLive(id),
    // Required for `focusOnEdge` to be anything but a no-op: without the links of the current
    // snapshot it cannot tell "this edge exists" from "these two nodes exist", so it refuses to
    // move the camera at all. Read live (not captured) for the same reason as the other five
    // `getLayoutLinks` call sites in this file: `layout.links` is replaced wholesale per snapshot.
    getLayoutLinks: () => layout.links,
  })

  const cameraSystem = viewWiring.cameraSystem
  const camera = viewWiring.camera
  const panState = viewWiring.panState

  getCameraZoomSafe = () => camera.zoom

  const resetCamera = viewWiring.resetCamera
  const worldToScreen = viewWiring.worldToScreen
  const screenToWorld = viewWiring.screenToWorld
  const clientToScreen = viewWiring.clientToScreen

  const selectedNodeScreenCenter = viewWiring.selectedNodeScreenCenter

  function getNodeScreenCenter(nodeId: string): Point | null {
    if (!nodeId) return null
    const node = getLayoutNodeByIdLive(nodeId)
    if (!node) return null
    return worldToScreen(node.__x, node.__y)
  }

  const selectedNodeEdgeStats = useSelectedNodeEdgeStats({
    getSnapshot: () => state.snapshot,
    getSelectedNodeId: () => state.selectedNodeId,
  }).selectedNodeEdgeStats

  const fxOverlays = useAppFxOverlays({
    getLayoutNodeById: (id) => getLayoutNodeByIdLive(id) ?? undefined,
    sizeForNode,
    getCameraZoom: () => camera.zoom,
    layoutVersion: layoutCoordinator.layoutVersion,
    isWebDriver: () => isWebDriver,
    getLayoutNodes: getLayoutNodesLive,
    worldToScreen,
    // Important: FX timers must wake up render loop before mutating FX state.
    wakeUp,
  })

  // Render-loop callbacks must be safe even before physics/drag wiring is initialized.
  // Use replaceable getters to avoid temporal coupling / TDZ hazards.
  type DragToPinStateSafe = { active: boolean; dragging: boolean; nodeId: string | null }
  let getDragToPinStateSafe = (): DragToPinStateSafe => ({ active: false, dragging: false, nodeId: null })
  // Bound once the drag owner exists (it is created after the render loop).
  let syncDragWithSceneSafe: () => void = () => undefined
  let tickPhysicsAndSyncSafe = (): boolean => false
  let isPhysicsRunningSafe = (): boolean => false

  const renderLoop = useAppRenderLoop({
    canvasEl,
    fxCanvasEl,
    getSnapshot: () => state.snapshot,
    getLayout: () => ({
      w: layout.w,
      h: layout.h,
      nodes: layout.nodes,
      links: layout.links,
    }),
    getCamera: () => camera,
    isTestMode: () => isTestMode.value,
    getQuality: () => quality.value,
    isGpuLikely: () => gpuAccelLikely.value,
    pruneActiveEdges: fxOverlays.pruneActiveEdges,
    pruneActiveNodes: fxOverlays.pruneActiveNodes,
    pruneFloatingLabels: fxOverlays.pruneFloatingLabels,
    hasFloatingLabels: fxOverlays.hasFloatingLabels,
    mapping: vizMapping,
    fxState: fxOverlays.fxState,
    getSelectedNodeId: () => state.selectedNodeId,
    activeEdges: fxOverlays.activeEdges,
    activeNodes: fxOverlays.activeNodes,
    // Use 'focus' LOD during drag OR while physics is settling — both enable dragMode fast-path in baseGraph.
    getLinkLod: () => {
      const ds = getDragToPinStateSafe()
      if (ds.active && ds.dragging) return 'focus'
      if (isPhysicsRunningSafe()) return 'focus'
      return 'full'
    },
    getHiddenNodeId: () => {
      const ds = getDragToPinStateSafe()
      return ds.active && ds.dragging ? ds.nodeId : null
    },
    beforeDraw: () => {
      // 039 slice B: a drag follows its node (patch, theme) or ends with the scene it was over - see `syncDragWithScene`.
      syncDragWithSceneSafe()
      const didTick = tickPhysicsAndSyncSafe()
      const ds = getDragToPinStateSafe()
      if (didTick || (ds.active && ds.dragging)) layoutCoordinator.bumpLayoutVersion()
    },
    isAnimating: createSimulatorIsAnimating({
      isPhysicsRunning: () => isPhysicsRunningSafe(),
    }),
    // BUG-3 (P3): dim unavailable nodes during picking phases.
    getDimmedNodeIds: () => {
      const avail = interactMode.availableTargetIds.value
      // Tri-state: `undefined` => unknown, do not dim/highlight.
      if (!avail) return null
      return avail.size > 0 ? avail : null
    },
  })

  // Bind after render loop is created.
  wakeUpImpl = renderLoop.wakeUp

  const fxState = fxOverlays.fxState
  const hoveredEdge = fxOverlays.hoveredEdge
  const clearHoveredEdge = fxOverlays.clearHoveredEdge
  const addActiveEdge = fxOverlays.addActiveEdge
  const addActiveNode = fxOverlays.addActiveNode
  const pushFloatingLabel = fxOverlays.pushFloatingLabel
  const pushClearingAmountOverlay = fxOverlays.pushClearingAmountOverlay
  const resetOverlays = fxOverlays.resetOverlays
  const floatingLabelsViewFx = fxOverlays.floatingLabelsViewFx
  const scheduleTimeout = fxOverlays.scheduleTimeout
  const clearScheduledTimeouts = fxOverlays.clearScheduledTimeouts

  /**
   * 039 slice C: the visible length of a line on screen, between its link terminations at the current zoom - what
   * the beam and the clearing spark keep one speed by (`txRoutePlan.ts`). null when a node is not laid out.
   */
  function edgeLengthPx(from: string, to: string): number | null {
    const a = getLayoutNodeById(from)
    const b = getLayoutNodeById(to)
    if (!a || !b) return null
    const zoom = Math.max(0.01, Number(camera.zoom) || 1)
    const s = getLinkTermination(a, b, 1 / zoom)
    const sx = s.x
    const sy = s.y
    const e = getLinkTermination(b, a, 1 / zoom)
    return Math.hypot(e.x - sx, e.y - sy) * zoom
  }

  const realClearingFx = useRealClearingFx({
    fxState,
    isTestMode,
    isWebDriver,
    keyEdge,
    seedFn: fnv1a,
    // Read at spawn time: `vizMapping.fx` is replaced on a theme change (039 slice C, BACKLOG 039-5 (zh)).
    clearingColor: () => vizMapping.fx.clearing_debt,
    sparkCoreColor: () => vizMapping.fx.tx_spark.core,
    addActiveNode,
    addActiveEdge,
    pushClearingAmountOverlay,
    clearingLabelLifeMs: () => vizMapping.motion.clearingLabelMs,
    scheduleTimeout,
    getLayoutNodeById,
    edgeLengthPx,
    spawnEdgePulses,
    spawnNodeBursts,
    spawnSparks,
  })

  runClearingFxImpl = realClearingFx.runClearingFx
  runRealClearingDoneFxImpl = realClearingFx.runRealClearingDoneFx
  resetClearingFxDedupImpl = realClearingFx.resetDedup

  const ensureRenderLoop = renderLoop.ensureRenderLoop
  const stopRenderLoop = renderLoop.stopRenderLoop
  const renderOnce = renderLoop.renderOnce

  // BUG-4: highlight available picking targets via activeNodes glow.
  // Watch uses a short TTL so nodes fade naturally once the picking phase ends.
  watch(
    () => interactMode.availableTargetIds.value,
    (ids) => {
      // Tri-state: `undefined` => unknown, do not dim/highlight.
      if (!ids || ids.size === 0) return
      const PICKING_HIGHLIGHT_TTL_MS = 800
      for (const id of ids) addActiveNode(id, PICKING_HIGHLIGHT_TTL_MS)
    },
    { immediate: false },
  )

  const showPerfOverlay = computed(() => {
    if (isTestMode.value) return false
    const w = typeof window !== 'undefined' ? window : undefined
    if (!w) return false
    try {
      return new URLSearchParams(w.location.search).get('perf') === '1'
    } catch {
      return false
    }
  })

  const perf = reactive({
    lastFps: null as number | null,
    fxBudgetScale: null as number | null,
    maxParticles: null as number | null,
    renderQuality: null as string | null,
    dprClamp: null as number | null,

    canvasCssW: null as number | null,
    canvasCssH: null as number | null,
    canvasPxW: null as number | null,
    canvasPxH: null as number | null,
    canvasDpr: null as number | null,
  })

  let perfTimer: number | null = null
  onMounted(() => {
    if (!showPerfOverlay.value) return

    const poll = () => {
      perf.lastFps = typeof fxState.__lastFps === 'number' ? fxState.__lastFps : null
      perf.fxBudgetScale = typeof fxState.__fxBudgetScale === 'number' ? fxState.__fxBudgetScale : null
      perf.maxParticles = typeof fxState.__maxParticles === 'number' ? fxState.__maxParticles : null
      perf.renderQuality = typeof fxState.__renderQuality === 'string' ? fxState.__renderQuality : null
      perf.dprClamp = typeof fxState.__dprClamp === 'number' ? fxState.__dprClamp : null

      const c = canvasEl.value
      if (c) {
        perf.canvasCssW = c.clientWidth || null
        perf.canvasCssH = c.clientHeight || null
        perf.canvasPxW = c.width || null
        perf.canvasPxH = c.height || null
        perf.canvasDpr = c.clientWidth ? c.width / Math.max(1, c.clientWidth) : null
      }
    }

    poll()
    perfTimer = window.setInterval(poll, 500)
  })

  onUnmounted(() => {
    if (perfTimer !== null) window.clearInterval(perfTimer)
    perfTimer = null
  })

  const realPatchApplier = createPatchApplier({
    getSnapshot: () => state.snapshot,
    getLayoutNodes: () => layout.nodes,
    getLayoutLinks: () => layout.links,
    keyEdge,
  })

  const physicsAndPinning = useAppPhysicsAndPinningWiring({
    isEnabled: () => !isTestMode.value,
    getLayoutNodes: () => layout.nodes,
    getLayoutLinks: () => layout.links,
    getQuality: () => quality.value,
    getSelectedNodeId: () => state.selectedNodeId,
    getLayoutNodeById: (id) => getLayoutNodeById(id),
    wakeUp,
  })

  const physics = physicsAndPinning.physics
  const pinning = physicsAndPinning.pinning
  const pinnedPos = physicsAndPinning.pinnedPos
  const isSelectedPinned = physicsAndPinning.isSelectedPinned
  const isNodePinned = physicsAndPinning.isPinned
  const pinNode = physicsAndPinning.pinNode
  const unpinNode = physicsAndPinning.unpinNode
  const pinSelectedNode = physicsAndPinning.pinSelectedNode
  const unpinSelectedNode = physicsAndPinning.unpinSelectedNode

  tickPhysicsAndSyncSafe = () => physics.tickAndSyncToLayout()
  isPhysicsRunningSafe = () => physics.isRunning()

  layoutWiring.initComputeLayout({
    pinning,
    physics,
  })

  function pushFloatingLabelWhenReady(
    // This helper is node-anchored only: nodeId is required.
    // World-anchored clearing amount overlays must use pushClearingAmountOverlay instead.
    opts: Parameters<typeof pushFloatingLabel>[0] & { nodeId: string },
    retryLeft = 6,
    retryDelayMs = 90,
  ) {
    // Overlay state is now tolerant to nodes missing from layout (it won't drop labels).
    // Keep the retry mechanism to avoid "label appears later but immediately expires" in edge cases,
    // but if we run out of retries, still push once so it can render when the node shows up.
    if (getLayoutNodeById(opts.nodeId) || retryLeft <= 0) {
      pushFloatingLabel(opts)
      return
    }

    scheduleTimeout(() => pushFloatingLabelWhenReady(opts, retryLeft - 1, retryDelayMs), retryDelayMs)
  }

  const realTxFx = useRealTxFx({
    fxState,
    isTestMode,
    isWebDriver,
    intensityScale,
    resolveTxDirection,
    keyEdge,
    seedFn: fnv1a,
    fxColors: () => ({ txSparkCore: vizMapping.fx.tx_spark.core, txSparkTrail: vizMapping.fx.tx_spark.trail, clearingDebt: vizMapping.fx.clearing_debt }),
    fxColorForNode,
    edgeLengthPx,
    txLabelLifeMs: () => vizMapping.motion.txLabelMs,
    scheduleTimeout,
    pushFloatingLabelWhenReady,
  })

  const pushTxAmountLabel = realTxFx.pushTxAmountLabel
  const runRealTxFx = realTxFx.runRealTxFx
  const routeArrivalMs = realTxFx.routeArrivalMs

  // ── 042 T4205: the offline effects workbench ─────────────
  // The recorded playlists of the offline snapshot, played through the live event path with the FX functions above
  // (`usePlaylistWorkbench.ts`). Offline source only, and not on the screenshot stand (its pictures read no playlist).
  let playlistWorkbenchActivePlayback: () => string | null = () => null
  const deliverPlaylistEvent = createLivePathDelivery({
    state,
    patchApplier: realPatchApplier,
    isUserFacingRunError,
    inc,
    getActivePlaybackId: () => playlistWorkbenchActivePlayback(),
    optionalFxEnabled: opts?.optionalFxEnabled,
    wakeUp,
    runRealTxFx,
    runRealClearingDoneFx,
    pushTxAmountLabel,
    routeArrivalMs,
    scheduleTimeout,
  })
  // 039 T3935: a recording is played on its own start snapshot. The offline source loads through this source (see
  // `loadSnapshotForUi`), which gives the recording's snapshot only between the workbench's `enter` and `leave`.
  let reloadSceneForRecording: () => Promise<void> = async () => undefined
  const recordingSceneSource = createRecordingSceneSource({
    loadOfflineSnapshot: loadSnapshotFixtures,
    loadSnapshotAt: loadSnapshotFixturesAt,
    reloadScene: () => reloadSceneForRecording(),
    getSceneSourcePath: () => state.sourcePath,
  })
  const playlistWorkbench = usePlaylistWorkbench({
    isOfflineSource: () => !isRealMode.value && !isE2eScreenshots.value,
    getSnapshotEquivalent: () => state.snapshot?.equivalent ?? null,
    getSceneIdentity: () => state.snapshot,
    fetchJson: (path) => loadJson<unknown>(path),
    base: fixturesBase(),
    deliver: deliverPlaylistEvent,
    // A playback is a new "run" for the effect caches (a replayed clearing is not deduplicated against the last one);
    // an ended one invalidates its pending beam legs and receiver burst.
    fx: { resetTxFx: () => realTxFx.resetRateLimiter(), resetClearingDedup: () => resetClearingFxDedupImpl() },
    // One duration source (039 slice C, P-3): the effects say how long they last; the recording's scene stays until the
    // last amount label has faded (039 T3935).
    effects: {
      tx: realTxFx.routeEffectMs,
      clearingMs: CLEARING_EFFECT_MS,
      afterLastMs: Math.max(vizMapping.motion.txLabelMs, vizMapping.motion.clearingLabelMs),
    },
    recordingScene: {
      // The recording's graph is a new scene: its layout is laid out afresh and the physics settles it for a few seconds
      // (measured 2026-10-18: ~4.4 s, nodes moving up to ~25 px per 250 ms). The playback waits for the physics to
      // stop, at most RECORDING_SCENE_SETTLE_MAX_MS, so its effects play on a still graph as they did on the offline one.
      enter: async (equivalent, sourcePath) => {
        if (!(await recordingSceneSource.enter(equivalent, sourcePath))) return false
        const until = performance.now() + RECORDING_SCENE_SETTLE_MAX_MS
        while (physics.isRunning() && performance.now() < until && recordingSceneSource.shown() === sourcePath) {
          await new Promise<void>((resolve) => setTimeout(resolve, 50))
        }
        return state.sourcePath === sourcePath
      },
      leave: recordingSceneSource.leave,
    },
  })
  playlistWorkbenchActivePlayback = playlistWorkbench.activePlaybackId

  const labelNodes = useLabelNodes({
    isTestMode: () => isTestMode.value,
    getLabelsLod: () => labelsLod.value,
    getSnapshot: () => state.snapshot,
    getSelectedNodeId: () => state.selectedNodeId,
    getLayoutLinks: () => layout.links,
    getLayoutNodeById: (id) => getLayoutNodeByIdLive(id),
    getNodeById,
    getCameraZoom: () => camera.zoom,
    sizeForNode: (n) => sizeForNode(n),
    hasNodeCardInspectorOpen: () => hasNodeCardInspectorOpen.value,
    showPid: participantDisplayIdFor(() => shownScenarioId.value),
  }).labelNodes

  const pickingAndHover = useAppPickingAndHover({
    hostEl,
    canvasEl,
    getLayoutNodes: getLayoutNodesLive,
    getLayoutLinks: () => layout.links,
    getCameraZoom: () => camera.zoom,
    clientToScreen,
    screenToWorld,
    worldToScreen,
    sizeForNode,
    hoveredEdge,
    clearHoveredEdge,
    isWebDriver: () => isWebDriver,
    getSelectedNodeId: () => state.selectedNodeId,
    hasSelectedIncidentEdges: () => selectedIncidentEdgeKeys.value.size > 0,
    getLinkByKey: (k) => layoutLinkMap.value.get(k),
    getUnit: () => state.snapshot?.equivalent ?? effectiveEq.value,
  })

  const pickNodeAt = pickingAndHover.pickNodeAt
  const pickEdgeAt = pickingAndHover.pickEdgeAt
  const edgeHover = pickingAndHover.edgeHover

  const isInteractPickingPhase = computed(() => {
    if (!isLiveUi.value) return false
    return !!interactMode.isPickingPhase.value
  })

  const isInteractCanvasNodePickPhase = computed(() => {
    if (!isLiveUi.value) return false
    return !!interactMode.isCanvasNodePickPhase.value
  })

  /**
   * Window management MVP (Step 0): close the topmost outside-click-closable overlay.
   *
   * Priority:
   *  1) EdgeDetailPopup (if open)
   *  2) NodeCardOverlay (if open)
    *
    * NOTE: interact panels are closed via `cancelInteract()` in Step 0.
    * This callback is responsible only for inspector window dismissal.
   */
  function closeTopmostOverlayOnOutsideClick(): OutsideClickOverlayKey | null {
    // WM-only runtime: outside-click closure is owned by the root WindowManager layer.
    if (typeof opts?.uiCloseTopmostInspectorWindow === 'function') {
      return opts.uiCloseTopmostInspectorWindow()
    }

    // Safety fallback for tests/miswiring: no-op.
    return null
  }

  function selectNodeFromCanvas(id: string | null) {
    __selectNodeFromCanvasStep0({
      id,
      isInteractCanvasNodePickPhase: isInteractCanvasNodePickPhase.value,
      interactSelectNode: (nodeId) => interactMode.selectNode(nodeId),
      isInteractBusy: !!interactMode.busy.value,
      confirmCancelInteractBusy: () => {
        try {
          const c = window.confirm
          if (typeof c !== 'function') return true
          return !!c('Отменить операцию?')
        } catch {
          return true
        }
      },

      closeTopmostOverlayOnOutsideClick: () => {
        void closeTopmostOverlayOnOutsideClick()
      },
      // 037 C: an empty click cancels what is being filled in, not a held result (see `cancelFromCanvas`).
      cancelInteract: () => interactMode.cancelFromCanvas(),
      // 043 S2 (fix-delta 1): a click on a participant is an explicit act - it pauses every time, the same participant
      // clicked again after "Поток" too (the selection does not change then, so no watch of it can see the act).
      selectNode: (nodeId) => {
        selectNode(nodeId)
        if (nodeId) noteManualAction()
      },
    })
  }

  function openNodeCardFromCanvasDblClick(nodeId: string) {
    const id = nodeId.trim()
    if (!id) return

    // Dblclick semantics: select node and open its card.
    selectNode(id)

    if (typeof opts?.uiOpenOrUpdateNodeCard === 'function') {
      opts.uiOpenOrUpdateNodeCard({ nodeId: id, anchor: getNodeScreenCenter(id) })
    }
  }

  const { dragToPin, hideDragPreview, syncDragWithScene, cancelDrag } = useAppDragToPinAndPreview({
    dragPreviewEl,
    isTestMode: () => isTestMode.value,

    pickNodeAt,
    getLayoutNodeById: (id) => layoutIndex.value.nodeById.get(id) ?? null,
    // 043 S2 (adversarial 1-A): pressing on a participant to drag it is an explicit act - it pauses like a click (the
    // click the browser fires after a drag is suppressed, so the canvas click entry never runs for a drag).
    setSelectedNodeId: (id) => {
      selectNode(id)
      if (id) noteManualAction()
    },
    clearHoveredEdge,

    clientToScreen,
    screenToWorld,
    getCanvasEl: () => canvasEl.value,

    renderOnce,

    pinNodeLive: (id, x, y) => physics.pin(id, x, y),
    commitPinnedPos: (id, x, y) => pinnedPos.set(id, { x, y }),
    reheatPhysics: (alpha) => physics.reheat(alpha),

    getNodeById,
    getCamera: () => camera,
    sizeForNode,
    fillForNode: (n) => fillForNode(n, vizMapping),
    lookForNode: (n) => classifyNode(n),
    hubColor: () => vizMapping.node.palette.hub,
  })

  getDragToPinStateSafe = () => dragToPin.dragState
  syncDragWithSceneSafe = syncDragWithScene

  const canvasInteractionsWiring = useAppCanvasInteractionsWiring({
    isTestMode: () => isTestMode.value,
    wakeUp,
    pickNodeAt,
    pickEdgeAt,
    selectNode: selectNodeFromCanvas,
    onNodeDblClick: (node) => {
      openNodeCardFromCanvasDblClick(node.id)
      return true
    },
    clearHoveredEdge,
    onEdgeClick: (edge, ptr) => {
      if (!isLiveUi.value) return false
      if (apiMode.value !== 'real') return false

      // Interact UI: edge click opens trustline editing.
      // IMPORTANT: anchor must be in the same coordinate system as popup clamping/placement.
      // Use host-relative screen coordinates (clientToScreen).
      const anchor = hostEl.value ? clientToScreen(ptr.clientX, ptr.clientY) : { x: ptr.clientX, y: ptr.clientY }
      // Busy rejection is still a handled canvas click: do not fall through to
      // the outside-click path, which can prompt and cancel the in-flight flow.
      if (!interactMode.selectEdge(edge.key, anchor)) return true

      // Step 5 (WM): open/update edge-detail window.
      // `edge.*Id` values are participant ids in this UI; treat them as pids.
      opts?.uiOpenOrUpdateEdgeDetail?.({ fromPid: edge.fromId, toPid: edge.toId, anchor })

      selectNode(edge.fromId)
      return true
    },
    dragToPin,
    cameraSystem,
    edgeHover,
    getPanActive: () => panState.active,
  })

  // Dblclick is always reserved for opening NodeCard (even during picking).
  // Single click is the flow input in picking phases.

  /**
   * Which run (and under which selected scenario) each run-snapshot source path was loaded for. Written when the
   * read FINISHES, read only through `state.sourcePath` - which the scene owner sets when it ACCEPTS a snapshot - so
   * a late load that the owner rejected is never "the scene on screen". A handful of entries (run x equivalent).
   */
  const runSceneLoads = new Map<string, { runId: string; scenarioId: string }>()
  function recordRunSceneLoad(sourcePath: string, runId: string, requestedUnderScenarioId: string) {
    // The scenario the read was REQUESTED under, taken before the await: a read that finishes later (and that the
    // owner rejects) must not rewrite the record of the scene that was accepted for the same key.
    runSceneLoads.set(sourcePath, { runId, scenarioId: requestedUnderScenarioId })
  }

  const previewSourcePath = (scenarioId: string, eq: string) =>
    `GET ${real.apiBase}/simulator/scenarios/${encodeURIComponent(scenarioId)}/graph/preview?equivalent=${encodeURIComponent(eq)}&mode=real`
  // The source the LATEST scene load asked for, as the `sourcePath` its snapshot would carry. Written when the load
  // is requested (before its first await); the scene owner reads it only for the latest load, when that load failed.
  let requestedSceneSource: string | null = null

  async function loadSnapshotForUi(eq: string): Promise<{ snapshot: GraphSnapshot; sourcePath: string }> {
    requestedSceneSource = null
    if (!isRealMode.value) return recordingSceneSource.load(eq)

    const runId = real.runId
    const scenarioId = real.selectedScenarioId

    // An UNKNOWN status (null: not read yet, or its read failed - a timeout included) is not "no active run" for a
    // run whose scene is on screen: that scene is not demoted to the preview, the run snapshot is read instead
    // (034 S5b). Only a REFRESH of that scene: choosing another scenario is an explicit request for its preview
    // (the scenario watcher allows it while the status is unknown), and a known terminal status (stopped/error)
    // still ends the run scene.
    const shownRunScene = runSceneLoads.get(state.sourcePath)
    const statusUnknownForShownRun =
      !!runId && !real.runStatus && shownRunScene?.runId === runId && shownRunScene.scenarioId === scenarioId
    const isActiveRun =
      !!runId &&
      // Otherwise treat as active only when we have a known status.
      // This avoids getting stuck on an unreachable/stale runId (scenario switching should still show previews).
      ((!!real.runStatus && isLiveRunState(real.runStatus.state)) || statusUnknownForShownRun)

    if (isActiveRun) requestedSceneSource = runSnapshotSourcePath(real.apiBase, runId, eq)
    else if (scenarioId) requestedSceneSource = previewSourcePath(scenarioId, eq)

    // Real mode: if we have a run, use run snapshot
    if (isActiveRun) {
      const fromRun = await loadActiveRunSnapshot({
        apiBase: real.apiBase,
        accessToken: real.accessToken,
        runId,
        equivalent: eq,
        onStaleRun: () => resetStaleRunThroughOwner({ clearError: true }),
      })
      if (fromRun) {
        recordRunSceneLoad(fromRun.sourcePath, runId, scenarioId)
        return fromRun
      }
    }

    // Real mode: no run, but have scenario selected - show preview
    // A preview that fails is THROWN (034 S8, F-034-D2): an empty network with no debts is data, and a failed
    // read is not. The scene owner keeps what is shown and puts the error into `state.error`.
    if (scenarioId) {
      const snapshot = await getScenarioPreview({ apiBase: real.apiBase, accessToken: real.accessToken }, scenarioId, eq)
      if (!snapshot.limits) snapshot.limits = { max_particles: 220 }
      return {
        snapshot,
        sourcePath: previewSourcePath(scenarioId, eq),
      }
    }

    // Nothing is selected to read (no run, no scenario): an empty scene, not a failure.
    return {
      snapshot: {
        equivalent: eq,
        generated_at: new Date().toISOString(),
        nodes: [],
        links: [],
      },
      sourcePath: 'No run started',
    }
  }

  let handleRealSceneContextChange: () => Promise<void> = async () => undefined
  const sceneState = useAppSceneState({
    eq,
    layoutMode,
    effectiveEq,
    state,
    isTestMode: () => isTestMode.value,
    isEqAllowed: (v) => EQUIVALENT_CODE_RE.test(String(v ?? '').toUpperCase()),
    loadSnapshot: loadSnapshotForUi,
    requestedSceneSource: () => requestedSceneSource,
    loadRecoverySnapshot: async ({ runId, equivalent }) => {
      const requestedUnderScenarioId = real.selectedScenarioId
      const loaded = await loadStrictRunRecoverySnapshot({
        apiBase: real.apiBase,
        accessToken: real.accessToken,
        runId,
        equivalent,
      })
      recordRunSceneLoad(loaded.sourcePath, runId, requestedUnderScenarioId)
      return loaded
    },
    onIncrementalSnapshotLoaded: (snapshot) => syncLayoutFromSnapshot(snapshot),
    clearScheduledTimeouts,
    resetCamera,
    resetLayoutKeyCache: () => layoutCoordinator.resetLayoutKeyCache(),
    // What `clearScene` drops with the scene: the FX overlays, and (039 slice B) a drag in progress - its preview
    // is a picture of a node of the scene that is being replaced.
    resetOverlays: () => {
      resetOverlays()
      cancelDrag()
    },
    resizeAndLayout,
    ensureRenderLoop,
    setupResizeListener: () => layoutCoordinator.setupResizeListener(),
    teardownResizeListener: () => layoutCoordinator.teardownResizeListener(),
    stopRenderLoop,
    // In real mode the initial snapshot load is handled by useSimulatorRealMode's
    // immediate watcher (refreshScenarios → refreshSnapshot). Skipping the duplicate
    // loadScene() in setup() eliminates a redundant "Loading…" flash on page open.
    skipInitialLoad: () => isRealMode.value,
    onSceneContextChange: () => handleRealSceneContextChange(),
  })
  reloadSceneForRecording = () => sceneState.loadScene()

  function cleanupRealRunFxAndTimers() {
    // Keep critical timers (e.g. “arrival” labels) so quick stop/restart/cleanup doesn't silently drop them.
    // Each critical timer must self-guard (runId / sseSeq / abort signal) before emitting UI.
    clearScheduledTimeouts({ keepCritical: true })
    resetOverlays()
    clearHoveredEdge()

    // Clear per-run dedup caches (avoid cross-run suppression).
    resetClearingFxDedupImpl()

    // Reset token-bucket between runs so a newly started run can always show TX FX immediately.
    realTxFx.resetRateLimiter()

    physics.stop()
  }

  const realMode = useSimulatorRealMode({
    isRealMode,
    isLocalhost,
    effectiveEq,
    state,
    real,
    ensureScenarioSelectionValid,
    resetRunStats,
    cleanupRealRunFxAndTimers,
    isUserFacingRunError,
    inc,
    loadScene: () => sceneState.loadScene(),
    loadRecoveryScene: (context) => sceneState.loadRecoveryScene(context),
    realPatchApplier,
    pushTxAmountLabel,
    runRealTxFx,
    routeArrivalMs,
    runRealClearingDoneFx,
    scheduleTimeout,
    wakeUp,
    optionalFxEnabled: opts?.optionalFxEnabled,
  })
  handleRealSceneContextChange = realMode.handleSceneContextChange
  resetStaleRunOwner = realMode.resetStaleRun

  // 043 S2: the Interact contract "intensity forced to 0, local-only" (`33563ba2`) is gone with the mode. What keeps the
  // automatic payments out of a person's way now is the pause on a manual action (decision 3, below).

  /**
   * 043 S2 (decision 3): "ручной режим должен останавливать все платежи". An explicit manual action pauses the run
   * (`/pause`): the entries wrapped where `interactMode` is created, a participant chosen (below), and the full line
   * editor of the root (`realActions.pauseForManualAction`). Resumed only by an explicit act of the person - "Поток" or
   * Resume under "Подробнее"; confirm, cancel, Escape and a closed panel never resume. Triggered by the ACT, not while
   * an action lasts: a person who brings the flow back with a panel open gets it, until their next act.
   */
  /**
   * 043 S2 (fix-delta 2): the evidence of a manual act comes ONLY from the explicit entries that call this - never from
   * state. A selection made by loading (the `?focus=` deep link, a snapshot load) is not an act: it neither pauses a
   * running run nor makes the opening start its run paused. Until this round a watch of `selectedNodeId` and the
   * predicate below read the selection, and a deep link paused a run nobody touched.
   */
  let manualEntryHappened = false
  function noteManualAction(): void {
    manualEntryHappened = true
    if (!isLiveUi.value) return
    void realMode.pauseForManualAction()
  }
  /** For the opening's start (`useInteractAutoBootstrapRun.ts`): has the person already acted on this page? */
  const isManualActionActive = () => manualEntryHappened

  // ── 043 S2 (decision 2): opening the demo is a live run ─────────────
  // One attempt, when the opening is over (the scene is on screen, so the boot has read the stored or the active run
  // and its status): no run, or an ended one - start the selected scenario with the existing `startRun`, not paused;
  // a live run - leave it as it is (a paused one stays paused). See `useInteractAutoBootstrapRun.ts`.
  const liveAutostart = useInteractAutoBootstrapRun({
    isLiveUi: () => isLiveUi.value,
    isManualActionActive,
    real,
    realMode: {
      startRun: (o) => realMode.startRun(o),
    },
  })

  if (isLiveUi.value) {
    const stopAutostartWatch = watch(
      () => !state.loading,
      async (opened) => {
        if (!opened) return
        stopAutostartWatch()
        try {
          await liveAutostart.onOpened()
        } catch (err) {
          // `startRun` records its own errors; this is anything else on the way (e.g. the scenario catalogue).
          console.warn('[autostart] failed:', err)
          __autoBootstrapMaybeFillUiError({ real, state, err })
        }
      },
      { flush: 'post' },
    )
  }

  const devHook = useGeoSimDevHookSetup({
    isDev: () => import.meta.env.DEV,
    isTestMode: () => isTestMode.value,
    isWebDriver: () => isWebDriver,
    getState: () => state,
    getCamera: () => ({ panX: camera.panX, panY: camera.panY, zoom: camera.zoom }),
    getLayoutNodes: () => layout.nodes,
    getPinnedNodeIds: () => [...pinnedPos.keys()],
    fxState,
    showEdgeTooltip: (edge) => {
      hoveredEdge.key = edge.key
      hoveredEdge.fromId = edge.fromId
      hoveredEdge.toId = edge.toId
      hoveredEdge.amountText = edge.amountText
      hoveredEdge.screenX = edge.screenX
      hoveredEdge.screenY = edge.screenY
      hoveredEdge.trustLimit = edge.trustLimit ?? null
      hoveredEdge.used = edge.used ?? null
      hoveredEdge.available = edge.available ?? null
      hoveredEdge.edgeStatus = edge.edgeStatus ?? null
      hoveredEdge.equivalent = edge.equivalent ?? null
      wakeUp()
    },
    hideEdgeTooltip: () => {
      clearHoveredEdge()
      wakeUp()
    },
    openNodeCard: opts?.uiOpenOrUpdateNodeCard,
    openEdgeDetail: opts?.uiOpenOrUpdateEdgeDetail,
  })

  useAppLifecycle({
    layoutMode,
    resizeAndLayout,
    persistedPrefs,
    setupDevHook: devHook.setupDevHook,
    disposeDevHook: devHook.disposeDevHook,
    sceneState,
    hideDragPreview,
    resetFxRendererCaches: () => {
      // Both modules are statically imported by this file, so both caches are reset synchronously:
      // nothing can read a stale cache between teardown and a late import callback.
      resetGlowSpritesCache()
      resetFxRendererCaches()
    },
    physics,
    getLayoutSize: () => ({ w: layout.w, h: layout.h }),
  })

  // ── Analytics overlay surface (spec 007, T705) ───────────────────────────────────────────────
  //
  // The toggle is owned and persisted by the mount point (`SimulatorAppRoot`), through the same
  // `useSimulatorStorage` mechanism as its bottom-bar neighbours. The second half of the condition
  // — real mode — is `__analyticsPanelVisibilityPolicy`, which is where the rule lives so that the
  // test that mounts the root can call it instead of re-inventing it. Mount point and poll gate
  // read the same computed and cannot disagree.
  const isAnalyticsPanelVisible = computed(() =>
    __analyticsPanelVisibilityPolicy(isRealMode.value, opts?.isAnalyticsPanelOpen?.() ?? false),
  )

  const analyticsStream = useMetricsPolling({
    apiBase: computed(() => real.apiBase),
    accessToken: computed(() => real.accessToken),
    runId: computed(() => real.runId),
    equivalent: effectiveEq,
    runStatus: computed(() => real.runStatus),
    /**
     * Exactly the panel's visibility, not merely "real mode": a hidden surface must not spend a
     * request pair every five seconds — `GET /metrics` and `GET /bottlenecks` both read the run's
     * metric tables. Absent (no surface declared itself) means no polling at all, which is the
     * only default that cannot bill a user for a screen nobody is looking at.
     */
    enabled: isAnalyticsPanelVisible,
  })

  const api = {
    apiMode,

    // flags
    isDemoFixtures,
    isLiveUi,
    isDebugUi,
    isTestMode,
    isWebDriver,
    isE2eScreenshots,

    // real mode
    real,
    realActions: {
      setApiBase: (v: string) => {
        real.apiBase = normalizeApiBase(v)
      },
      setAccessToken: (v: string) => {
        real.accessToken = v
      },
      setSelectedScenarioId: (v: string) => {
        real.selectedScenarioId = v
      },
      setIntensityPercent: (v: number) => {
        real.intensityPercent = Math.max(0, Math.min(100, Math.round(v)))
      },
      // 048 S1 (the owner's word, 2026-10-19): no UI refresh of the scenario catalogue - it is read at page load
      // (`useSimulatorRealMode.ts`, the boot's `refreshScenarios`); a reload picks up an uploaded scenario.
      startRun: realMode.startRun,
      pause: realMode.pause,
      // 043 S2: the entry the root makes without a flow transition (the full line editor of a line's popup).
      pauseForManualAction: async () => noteManualAction(),
      resume: realMode.resume,
      // 044 stage 2 (BACKLOG 043-23): a Stop pressed while the opening is still loading cancels its pending start -
      // decision 2, a run a person stopped is not started again behind them.
      stop: () => {
        liveAutostart.cancel()
        return realMode.stop()
      },
      applyIntensity: realMode.applyIntensity,
      refreshSnapshot: realMode.refreshSnapshot,
      // 048 S2 (amendment 9): no run files in the UI - the index and the downloads are the API's (`real-mode-runbook.md`).
    },

    // §10: Cookie session state (anonymous visitors)
    session: {
      actorKind: session.actorKind,
      ownerId: session.ownerId,
      bootstrapping: session.bootstrapping,
      tryEnsure: session.tryEnsure,
    },

    // 048 S2 (amendment 9): no admin menu; the attach by id is kept for its tests (see `SimulatorAppApi.admin`).
    admin: {
      attachRun: async (runId: string) => {
        await realMode.attachToRun(runId)
      },
    },

    // state + prefs
    state,
    eq,
    layoutMode,
    quality,
    labelsLod,

    effectiveEq,
    sceneEq,
    equivalentOptions,

    // derived interact UI helpers
    isInteractPickingPhase,
    isInteractCanvasNodePickPhase,

    // interact mode
    interact: {
      actions: interactActions,
      mode: interactMode,
      systemBalance,
    },

    // env
    gpuAccelLikely,

    dataPalette,
    resolveVizColors,

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

    // E2E-only (offline) actions for screenshot tests
    e2e: {
      runTxOnce: e2eTxOnce,
      runClearingOnce: e2eClearingOnce,
    },

    playlistWorkbench,

    // selection + overlays
    hasNodeCardInspectorOpen,
    hoveredEdge,
    clearHoveredEdge,
    addActiveEdge,
    edgeTooltipStyle: pickingAndHover.edgeTooltipStyle,
    selectedNode: viewWiring.selectedNode,
    selectedNodeScreenCenter,
    getNodeScreenCenter,
    selectedNodeEdgeStats,

    // pinning
    dragToPin,
    isSelectedPinned,
    isNodePinned,
    pinNode,
    unpinNode,
    pinSelectedNode,
    unpinSelectedNode,

    // handlers
    onCanvasClick: canvasInteractionsWiring.onCanvasClick,
    onCanvasDblClick: canvasInteractionsWiring.onCanvasDblClick,
    onCanvasPointerDown: canvasInteractionsWiring.onCanvasPointerDown,
    onCanvasPointerMove: canvasInteractionsWiring.onCanvasPointerMove,
    onCanvasPointerUp: canvasInteractionsWiring.onCanvasPointerUp,
    onCanvasWheel: canvasInteractionsWiring.onCanvasWheel,

    // render loop control (for wakeups from external events)
    wakeUp,

    // labels
    labelNodes,
    floatingLabelsViewFx,
    worldToCssTranslateNoScale: viewWiring.worldToCssTranslateNoScale,

    // analytics overlay surface (spec 007)
    analytics: {
      isVisible: isAnalyticsPanelVisible,
      metricsPhase: analyticsStream.metricsPhase,
      bottlenecksPhase: analyticsStream.bottlenecksPhase,
      metrics: analyticsStream.metrics,
      bottlenecks: analyticsStream.bottlenecks,
      metricsError: analyticsStream.metricsError,
      bottlenecksError: analyticsStream.bottlenecksError,
      metricsUnavailableReason: analyticsStream.metricsUnavailableReason,
      bottlenecksUnavailableReason: analyticsStream.bottlenecksUnavailableReason,
      isPolling: analyticsStream.isPolling,
    },

    // helpers for template
    getNodeById,
    focusOnEdge: viewWiring.focusOnEdge,
    resetView: viewWiring.resetView,
  } satisfies SimulatorAppApi

  return api
}
