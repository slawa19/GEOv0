import { computed, reactive, ref, watch } from 'vue'

import { loadEvents as loadEventsFixtures, loadSnapshot as loadSnapshotFixtures } from '../fixtures'
import { assertPlaylistEdgesExistInSnapshot } from '../demo/playlistValidation'
import { computeLayoutForMode, type LayoutMode } from '../layout/forceLayout'
import { fillForNode, sizeForNode } from '../render/nodePainter'
import type { ClearingDoneEvent, ClearingPlanEvent, GraphSnapshot, TxUpdatedEvent } from '../types'
import type { LabelsLod, Quality } from '../types/uiPrefs'
import type { SceneId } from '../scenes'
import { VIZ_MAPPING } from '../vizMapping'
import type { SimulatorAppState } from '../types/simulatorApp'
import { keyEdge } from '../utils/edgeKey'
import { fnv1a } from '../utils/hash'
import { createPatchApplier } from '../demo/patches'
import { spawnEdgePulses, spawnNodeBursts, spawnSparks } from '../render/fxRenderer'

import { getSnapshot, getScenarioPreview } from '../api/simulatorApi'
import type { ArtifactIndexItem, RunStatus, ScenarioSummary, SimulatorMode } from '../api/simulatorTypes'
import { normalizeApiBase } from '../api/apiBase'

import { useAppDemoPlayerSetup } from './useAppDemoPlayerSetup'
import { useAppLifecycle } from './useAppLifecycle'
import { useAppUiDerivedState } from './useAppUiDerivedState'
import { useLabelNodes } from './useLabelNodes'
import { useLayoutIndex } from './useLayoutIndex'
import { usePersistedSimulatorPrefs } from './usePersistedSimulatorPrefs'
import { useSelectedNodeEdgeStats } from './useSelectedNodeEdgeStats'
import { useSnapshotIndex } from './useSnapshotIndex'
import { useNodeSelectionAndCardOpen } from './useNodeSelectionAndCardOpen'
import { useAppSceneAndDemo } from './useAppSceneAndDemo'
import { useAppPickingAndHover } from './useAppPickingAndHover'
import { useAppFxAndRender } from './useAppFxAndRender'
import { useAppPhysicsAndPinningWiring } from './useAppPhysicsAndPinningWiring'
import { useAppLayoutWiring } from './useAppLayoutWiring'
import { useAppDragToPinWiring } from './useAppDragToPinWiring'
import { useAppCanvasInteractionsWiring } from './useAppCanvasInteractionsWiring'
import { useAppViewWiring } from './useAppViewWiring'
import { useSimulatorRealMode, type RealModeState } from './useSimulatorRealMode'

export function useSimulatorApp() {
  const eq = ref('UAH')
  const scene = ref<SceneId>('A')

  const layoutMode = ref<LayoutMode>('admin-force')

  const isDemoFixtures = computed(() => String(import.meta.env.VITE_DEMO_FIXTURES ?? '1') === '1')
  const isTestMode = computed(() => String(import.meta.env.VITE_TEST_MODE ?? '0') === '1')

  const apiMode = computed<'demo' | 'real'>(() => {
    try {
      const p = new URLSearchParams(window.location.search).get('mode')
      if (String(p ?? '').toLowerCase() === 'real') return 'real'
    } catch {
      // ignore
    }
    const env = String(import.meta.env.VITE_API_MODE ?? '').trim().toLowerCase()
    return env === 'real' ? 'real' : 'demo'
  })

  const isRealMode = computed(() => apiMode.value === 'real')

  // Playwright sets navigator.webdriver=true. Use it to keep screenshot tests stable even if
  // someone runs the dev server with VITE_TEST_MODE=1.
  const isWebDriver = typeof navigator !== 'undefined' && (navigator as any).webdriver === true

  const isLocalhost =
    typeof window !== 'undefined' && ['localhost', '127.0.0.1', '::1'].includes(window.location.hostname)

  const DEFAULT_REAL_SCENARIO_ID = 'greenfield-village-100'

  const ALLOWED_EQS = new Set(['UAH', 'HOUR', 'EUR'])

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

  const state = reactive<SimulatorAppState>({
    loading: true,
    error: '',
    sourcePath: '',
    eventsPath: '',
    snapshot: null,
    demoTxEvents: [],
    demoClearingPlan: null,
    demoClearingDone: null,
    selectedNodeId: null,
    flash: 0,
  })

  const DEFAULT_DEV_ACCESS_TOKEN = String(import.meta.env.VITE_GEO_DEV_ACCESS_TOKEN ?? 'dev-admin-token-change-me').trim()
  const ENV_ACCESS_TOKEN = String(import.meta.env.VITE_GEO_ACCESS_TOKEN ?? '').trim()

  const real = reactive<RealModeState>({
    apiBase: normalizeApiBase(lsGet('geo.sim.v2.apiBase', String(import.meta.env.VITE_GEO_API_BASE ?? '/api/v1'))),
    accessToken: lsGet(
      'geo.sim.v2.accessToken',
      ENV_ACCESS_TOKEN || (import.meta.env.DEV ? DEFAULT_DEV_ACCESS_TOKEN : ''),
    ),
    loadingScenarios: false,
    scenarios: [] as ScenarioSummary[],
    selectedScenarioId: lsGet('geo.sim.v2.selectedScenarioId', ''),
    desiredMode: (lsGet('geo.sim.v2.desiredMode', 'real') === 'fixtures' ? 'fixtures' : 'real') as SimulatorMode,
    // Default lower than demo to keep real-mode readable (avoid dozens of overlapping sparks).
    intensityPercent: Number(lsGet('geo.sim.v2.intensityPercent', '30')) || 30,
    runId: lsGet('geo.sim.v2.runId', '') || null,
    runStatus: null as RunStatus | null,
    sseState: 'idle',
    lastEventId: null as string | null,
    lastError: '',
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

  function inc(map: Record<string, number>, key: string) {
    map[key] = (map[key] ?? 0) + 1
  }

  function intensityScale(intensityKey?: string): number {
    const k = String(intensityKey ?? '').trim().toLowerCase()
    if (!k) return 1
    switch (k) {
      case 'muted':
      case 'low':
        return 0.75
      case 'active':
      case 'mid':
      case 'med':
        return 1
      case 'hi':
      case 'high':
        return 1.35
      default:
        return 1
    }
  }

  // tx.failed often represents a "clean rejection" (routing capacity, trustline constraints).
  // Those should not be surfaced as global "errors" in the HUD.
  function isUserFacingRunError(code: string): boolean {
    const c = String(code ?? '').toUpperCase()
    if (!c) return false
    if (c === 'PAYMENT_TIMEOUT') return true
    if (c === 'SENDER_NOT_FOUND') return true
    // Everything else is treated as a clean rejection for UX purposes.
    return false
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
    () => real.desiredMode,
    (v) => lsSet('geo.sim.v2.desiredMode', v),
  )
  watch(
    () => real.intensityPercent,
    (v) => lsSet('geo.sim.v2.intensityPercent', String(v)),
  )
  watch(
    () => real.runId,
    (v) => lsSet('geo.sim.v2.runId', v ?? ''),
  )

  const selectedNodeIdRef = computed<string | null>({
    get: () => state.selectedNodeId,
    set: (id) => {
      state.selectedNodeId = id
    },
  })

  const selectionAndCard = useNodeSelectionAndCardOpen({
    selectedNodeId: selectedNodeIdRef,
  })

  const isNodeCardOpen = selectionAndCard.isNodeCardOpen
  const selectNode = selectionAndCard.selectNode
  const setNodeCardOpen = selectionAndCard.setNodeCardOpen

  const canvasEl = ref<HTMLCanvasElement | null>(null)
  const fxCanvasEl = ref<HTMLCanvasElement | null>(null)
  const hostEl = ref<HTMLDivElement | null>(null)
  const dragPreviewEl = ref<HTMLDivElement | null>(null)

  const snapshotIndex = useSnapshotIndex({
    getSnapshot: () => state.snapshot,
  })

  const getNodeById = snapshotIndex.getNodeById

  function fxColorForNode(nodeId: string, fallback: string): string {
    const n = getNodeById(nodeId)
    if (!n) return fallback
    return fillForNode(n, VIZ_MAPPING)
  }

  const labelsLod = ref<LabelsLod>('selection')
  const quality = ref<Quality>('high')

  // uiDerived reads camera.zoom (for overlay label scale). Keep this wiring robust
  // by using a replaceable getter, so no TDZ/lazy-eval footgun is possible.
  let getCameraZoomSafe = () => 1
  const uiDerived = useAppUiDerivedState({
    eq,
    scene,
    quality,
    isTestMode,
    isWebDriver,
    getCameraZoom: () => getCameraZoomSafe(),
  })

  const effectiveEq = uiDerived.effectiveEq
  const dprClamp = uiDerived.dprClamp
  const showDemoControls = uiDerived.showDemoControls
  const showResetView = uiDerived.showResetView
  const overlayLabelScale = uiDerived.overlayLabelScale

  const snapshotRef = computed(() => state.snapshot)

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

  const persistedPrefs = usePersistedSimulatorPrefs({
    layoutMode,
    quality,
    labelsLod,
    requestResizeAndLayout,
  })

  const viewWiring = useAppViewWiring({
    canvasEl,
    hostEl,
    getLayoutNodes: () => layout.nodes,
    getLayoutW: () => layout.w,
    getLayoutH: () => layout.h,
    isTestMode: () => isTestMode.value,
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
    getLayoutNodeById: (id) => getLayoutNodeById(id),
    getNodeScreenSize: (n) => sizeForNode(n),
    getIncidentEdges: (nodeId) => layout.links.filter((l) => l.source === nodeId || l.target === nodeId),
  })

  const cameraSystem = viewWiring.cameraSystem
  const camera = viewWiring.camera
  const panState = viewWiring.panState

  getCameraZoomSafe = () => camera.zoom

  const resetCamera = viewWiring.resetCamera
  const worldToScreen = viewWiring.worldToScreen
  const screenToWorld = viewWiring.screenToWorld
  const clientToScreen = viewWiring.clientToScreen

  const worldToCssTranslateNoScale = viewWiring.worldToCssTranslateNoScale

  const selectedNode = viewWiring.selectedNode
  const nodeCardStyle = viewWiring.nodeCardStyle

  const selectedNodeEdgeStats = useSelectedNodeEdgeStats({
    getSnapshot: () => state.snapshot,
    getSelectedNodeId: () => state.selectedNodeId,
  }).selectedNodeEdgeStats

  const fxAndRender = useAppFxAndRender({
    canvasEl,
    fxCanvasEl,
    hostEl,
    getSnapshot: () => state.snapshot,
    getLayout: () => ({
      w: layout.w,
      h: layout.h,
      nodes: layout.nodes,
      links: layout.links,
    }),
    getLayoutNodes: () => layout.nodes,
    getLayoutNodeById: (id) => getLayoutNodeById(id) ?? undefined,
    getCamera: () => camera,
    worldToScreen,
    sizeForNode,
    isTestMode: () => isTestMode.value,
    isWebDriver: () => isWebDriver,
    getQuality: () => quality.value,
    getFlash: () => state.flash,
    setFlash: (v) => {
      state.flash = v
    },
    mapping: VIZ_MAPPING,
    getSelectedNodeId: () => state.selectedNodeId,
    getLinkLod: () => (dragToPin.dragState.active && dragToPin.dragState.dragging ? 'focus' : 'full'),
    getHiddenNodeId: () => (dragToPin.dragState.active && dragToPin.dragState.dragging ? dragToPin.dragState.nodeId : null),
    beforeDraw: () => {
      physics.tickAndSyncToLayout()
    },
  })

  const fxState = fxAndRender.fxState
  const hoveredEdge = fxAndRender.hoveredEdge
  const clearHoveredEdge = fxAndRender.clearHoveredEdge
  const activeEdges = fxAndRender.activeEdges
  const addActiveEdge = fxAndRender.addActiveEdge
  const pruneActiveEdges = fxAndRender.pruneActiveEdges
  const pushFloatingLabel = fxAndRender.pushFloatingLabel
  const resetOverlays = fxAndRender.resetOverlays
  const floatingLabelsViewFx = fxAndRender.floatingLabelsViewFx
  const scheduleTimeout = fxAndRender.scheduleTimeout
  const clearScheduledTimeouts = fxAndRender.clearScheduledTimeouts
  const ensureRenderLoop = fxAndRender.ensureRenderLoop
  const stopRenderLoop = fxAndRender.stopRenderLoop
  const renderOnce = fxAndRender.renderOnce

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
  })

  const physics = physicsAndPinning.physics
  const pinning = physicsAndPinning.pinning
  const pinnedPos = physicsAndPinning.pinnedPos
  const isSelectedPinned = physicsAndPinning.isSelectedPinned
  const pinSelectedNode = physicsAndPinning.pinSelectedNode
  const unpinSelectedNode = physicsAndPinning.unpinSelectedNode

  layoutWiring.initComputeLayout({
    pinning,
    physics,
  })

  const demoPlayerSetup = useAppDemoPlayerSetup({
    getSnapshot: () => state.snapshot,
    getLayoutNodes: () => layout.nodes,
    getLayoutLinks: () => layout.links,
    getLayoutNodeById: (id) => getLayoutNodeById(id) ?? undefined,
    fxState,
    pushFloatingLabel,
    resetOverlays,
    fxColorForNode,
    addActiveEdge,
    scheduleTimeout,
    clearScheduledTimeouts,
    isTestMode: () => isTestMode.value,
    isWebDriver,
    effectiveEq: () => effectiveEq.value,
  })

  const demoPlayer = demoPlayerSetup.demoPlayer
  const playlist = demoPlayerSetup.playlist

  // Real-mode tx visuals must be independent from demoPlayer.
  // demoPlayer.runTxEvent() ends with resetOverlays(), which clears FX and can make
  // sparks effectively invisible under steady SSE.
  type RealTxFxConfig = {
    ratePerSec: number
    burst: number
    maxConcurrentSparks: number
    maxEdgesPerEvent: number
    ttlMinMs: number
    ttlMaxMs: number
    activeEdgePadMs: number
    minGapMs: number
  }

  const REAL_TX_FX: RealTxFxConfig = {
    // Token bucket: how many tx events per second can spawn sparks.
    ratePerSec: 3.0,
    burst: 2.0,
    // Hard caps for perf.
    maxConcurrentSparks: 28,
    // Avoid animating long routes as a wall of particles.
    maxEdgesPerEvent: 2,
    // Clamp TTL to keep the scene from accumulating particles.
    ttlMinMs: 240,
    ttlMaxMs: 900,
    // Extra highlight time to make motion readable.
    activeEdgePadMs: 260,
    // Safety: prevent spamming even if tokens allow bursts.
    minGapMs: 120,
  }

  let txFxTokens = REAL_TX_FX.burst
  let txFxLastRefillAtMs = typeof performance !== 'undefined' ? performance.now() : Date.now()
  let txFxLastSpawnAtMs = 0

  function refillTxFxTokens(nowMs: number) {
    const dt = Math.max(0, nowMs - txFxLastRefillAtMs)
    txFxLastRefillAtMs = nowMs
    txFxTokens = Math.min(REAL_TX_FX.burst, txFxTokens + (dt * REAL_TX_FX.ratePerSec) / 1000)
  }

  function pickSparkEdges(edges: TxUpdatedEvent['edges']): Array<{ from: string; to: string }> {
    if (!edges || edges.length === 0) return []
    if (edges.length <= REAL_TX_FX.maxEdgesPerEvent) return edges

    const first = edges[0]!
    const last = edges[edges.length - 1]!
    if (first.from === last.from && first.to === last.to) return [first]
    return [first, last]
  }

  function runRealTxFx(evt: TxUpdatedEvent) {
    const nowMs = typeof performance !== 'undefined' ? performance.now() : Date.now()
    if (nowMs - txFxLastSpawnAtMs < REAL_TX_FX.minGapMs) return

    refillTxFxTokens(nowMs)
    if (txFxTokens < 1) return
    if (fxState.sparks.length >= REAL_TX_FX.maxConcurrentSparks) return

    const sparkEdges = pickSparkEdges(evt.edges)
    if (sparkEdges.length === 0) return

    txFxTokens -= 1
    txFxLastSpawnAtMs = nowMs

    const ttlRaw = Number(evt.ttl_ms ?? 1200)
    const ttlMs = Math.max(REAL_TX_FX.ttlMinMs, Math.min(REAL_TX_FX.ttlMaxMs, Number.isFinite(ttlRaw) ? ttlRaw : 1200))
    const k = intensityScale(evt.intensity_key)

    // Keep a short-lived highlight so the spark reads as motion along an edge.
    for (const e of sparkEdges) addActiveEdge(keyEdge(e.from, e.to), ttlMs + REAL_TX_FX.activeEdgePadMs)

    spawnSparks(fxState, {
      edges: sparkEdges,
      nowMs,
      ttlMs,
      colorCore: VIZ_MAPPING.fx.tx_spark.core,
      colorTrail: VIZ_MAPPING.fx.tx_spark.trail,
      thickness: 0.95 * k,
      kind: 'beam',
      seedPrefix: `real-tx:${evt.equivalent}`,
      countPerEdge: 1,
      keyEdge,
      seedFn: fnv1a,
      isTestMode: isTestMode.value && isWebDriver,
    })

    if (!(isTestMode.value && isWebDriver)) {
      const src = sparkEdges[0]!.from
      const dst = sparkEdges[sparkEdges.length - 1]!.to
      spawnNodeBursts(fxState, {
        nodeIds: [src],
        nowMs,
        durationMs: 280,
        color: fxColorForNode(src, VIZ_MAPPING.fx.tx_spark.trail),
        kind: 'tx-impact',
        seedPrefix: 'real-tx-src',
        seedFn: fnv1a,
        isTestMode: false,
      })
      scheduleTimeout(() => {
        spawnNodeBursts(fxState, {
          nodeIds: [dst],
          nowMs: typeof performance !== 'undefined' ? performance.now() : Date.now(),
          durationMs: 420,
          color: fxColorForNode(dst, VIZ_MAPPING.fx.tx_spark.trail),
          kind: 'tx-impact',
          seedPrefix: 'real-tx-dst',
          seedFn: fnv1a,
          isTestMode: false,
        })
      }, ttlMs)
    }
  }

  type RealClearingFxConfig = {
    highlightPulseMs: number
    highlightThickness: number
    microTtlMs: number
    microThickness: number
    nodeBurstMs: number
  }

  const REAL_CLEARING_FX: RealClearingFxConfig = {
    // Keep clearing highlights visible long enough to be noticed.
    highlightPulseMs: 5200,
    highlightThickness: 2.9,
    microTtlMs: 860,
    microThickness: 1.25,
    nodeBurstMs: 1100,
  }

  const clearingPlanFxStarted = new Set<string>()

  function safeBigInt(v: unknown): bigint {
    if (typeof v === 'bigint') return v
    if (typeof v === 'number' && Number.isFinite(v)) return BigInt(Math.trunc(v))
    if (typeof v === 'string') {
      try {
        return BigInt(v)
      } catch {
        return 0n
      }
    }
    return 0n
  }

  function signedBalanceForNodeId(nodeId: string): bigint {
    const n = getNodeById(nodeId)
    if (!n) return 0n
    const mag = n.net_balance_atoms == null ? 0n : safeBigInt(n.net_balance_atoms)
    if (mag < 0n) throw new Error(`Invalid snapshot: net_balance_atoms must be magnitude for node '${nodeId}'`)

    const s = typeof n.net_sign === 'number' && Number.isFinite(n.net_sign) ? n.net_sign : null
    if (s === 1) return mag
    if (s === -1) return -mag
    return 0n
  }

  function formatBigIntCompact(v: bigint): string {
    const abs = v < 0n ? -v : v
    const thousand = 1_000n
    const million = 1_000_000n
    const billion = 1_000_000_000n

    if (abs < thousand) return abs.toString()
    if (abs < million) return `${(abs / thousand).toString()}K`
    if (abs < billion) return `${(abs / million).toString()}M`
    return `${(abs / billion).toString()}B`
  }

  function pushFloatingLabelWhenReady(
    opts: Parameters<typeof pushFloatingLabel>[0],
    retryLeft = 6,
    retryDelayMs = 90,
  ) {
    if (retryLeft <= 0) return
    if (getLayoutNodeById(opts.nodeId)) {
      pushFloatingLabel(opts)
      return
    }
    scheduleTimeout(() => pushFloatingLabelWhenReady(opts, retryLeft - 1, retryDelayMs), retryDelayMs)
  }

  function pushBalanceDeltaLabels(
    beforeById: Map<string, bigint>,
    nodeIds: string[],
    unit: string,
    opts?: { throttleMs?: number },
  ) {
    for (const id of nodeIds) {
      const before = beforeById.get(id) ?? 0n
      const after = signedBalanceForNodeId(id)
      const delta = after - before
      if (delta === 0n) continue

      const sign = delta > 0n ? '+' : '-'
      const color = delta > 0n ? VIZ_MAPPING.fx.tx_spark.trail : VIZ_MAPPING.fx.clearing_debt
      pushFloatingLabelWhenReady({
        nodeId: id,
        text: `${sign}${formatBigIntCompact(delta)} ${unit}`,
        color: fxColorForNode(id, color),
        ttlMs: 1900,
        offsetYPx: -6,
        throttleKey: `bal:${id}`,
        throttleMs: opts?.throttleMs ?? 240,
      })
    }
  }

  function nodesFromEdges(edges: Array<{ from: string; to: string }>): string[] {
    const out: string[] = []
    const seen = new Set<string>()
    for (const e of edges) {
      if (!seen.has(e.from)) {
        seen.add(e.from)
        out.push(e.from)
      }
      if (!seen.has(e.to)) {
        seen.add(e.to)
        out.push(e.to)
      }
    }
    return out
  }

  function runRealClearingPlanFx(plan: ClearingPlanEvent) {
    const planId = String(plan?.plan_id ?? '')
    if (!planId) return
    if (clearingPlanFxStarted.has(planId)) return
    clearingPlanFxStarted.add(planId)

    const clearingColor = VIZ_MAPPING.fx.clearing_debt
    state.flash = Math.max(state.flash ?? 0, 0.75)

    for (const step of plan.steps ?? []) {
      const atMs = Math.max(0, Number(step.at_ms ?? 0))
      scheduleTimeout(() => {
        const nowMs = typeof performance !== 'undefined' ? performance.now() : Date.now()
        const k = intensityScale(step.intensity_key)
        const countPerEdge = k >= 1.0 ? 2 : 1

        const highlight = (step.highlight_edges ?? []).map((e) => ({ from: e.from, to: e.to }))
        if (highlight.length > 0) {
          spawnEdgePulses(fxState, {
            edges: highlight,
            nowMs,
            durationMs: REAL_CLEARING_FX.highlightPulseMs,
            color: clearingColor,
            thickness: REAL_CLEARING_FX.highlightThickness * k,
            seedPrefix: `real-clearing:highlight:${planId}:${plan.equivalent}:${step.at_ms}`,
            countPerEdge,
            keyEdge,
            seedFn: fnv1a,
            isTestMode: isTestMode.value && isWebDriver,
          })

          // Keep edges active even longer than the pulse itself.
          for (const e of highlight) addActiveEdge(keyEdge(e.from, e.to), REAL_CLEARING_FX.highlightPulseMs + 2200)
        }

        const particles = (step.particles_edges ?? []).map((e) => ({ from: e.from, to: e.to }))
        if (particles.length > 0) {
          for (const e of particles) addActiveEdge(keyEdge(e.from, e.to), REAL_CLEARING_FX.microTtlMs + 2200)

          spawnSparks(fxState, {
            edges: particles,
            nowMs,
            ttlMs: REAL_CLEARING_FX.microTtlMs,
            colorCore: VIZ_MAPPING.fx.tx_spark.core,
            colorTrail: clearingColor,
            thickness: REAL_CLEARING_FX.microThickness * k,
            kind: 'comet',
            seedPrefix: `real-clearing:micro:${planId}:${plan.equivalent}:${step.at_ms}`,
            countPerEdge: 1,
            keyEdge,
            seedFn: fnv1a,
            isTestMode: isTestMode.value && isWebDriver,
          })

          const nodeIds = nodesFromEdges(particles)
          spawnNodeBursts(fxState, {
            nodeIds,
            nowMs,
            durationMs: REAL_CLEARING_FX.nodeBurstMs,
            color: clearingColor,
            kind: 'clearing',
            seedPrefix: `real-clearing:nodes:${planId}:${step.at_ms}`,
            seedFn: fnv1a,
            isTestMode: isTestMode.value && isWebDriver,
          })
        }

        state.flash = Math.max(state.flash ?? 0, 0.38)
      }, atMs)
    }

    const maxAt = Math.max(
      0,
      ...((plan.steps ?? [])
        .map((s) => Number(s.at_ms ?? 0))
        .filter((v) => Number.isFinite(v))),
    )
    scheduleTimeout(() => {
      clearingPlanFxStarted.delete(planId)
    }, Math.max(1500, Math.min(30_000, maxAt + 2500)))
  }

  function runRealClearingDoneFx(plan: ClearingPlanEvent | undefined, done: ClearingDoneEvent) {
    const nowMs = typeof performance !== 'undefined' ? performance.now() : Date.now()
    const clearingColor = VIZ_MAPPING.fx.clearing_debt

    state.flash = 1

    const planEdges: Array<{ from: string; to: string }> = []
    if (plan?.steps) {
      for (const s of plan.steps) {
        for (const e of s.highlight_edges ?? []) planEdges.push({ from: e.from, to: e.to })
      }
    }
    if (planEdges.length > 0) {
      spawnEdgePulses(fxState, {
        edges: planEdges,
        nowMs,
        durationMs: 4200,
        color: clearingColor,
        thickness: 3.2,
        seedPrefix: `real-clearing:done:${done.plan_id}`,
        countPerEdge: 1,
        keyEdge,
        seedFn: fnv1a,
        isTestMode: isTestMode.value && isWebDriver,
      })

      for (const e of planEdges) addActiveEdge(keyEdge(e.from, e.to), 5200)

      const nodeIds = nodesFromEdges(planEdges)
      spawnNodeBursts(fxState, {
        nodeIds,
        nowMs,
        durationMs: 900,
        color: clearingColor,
        kind: 'clearing',
        seedPrefix: `real-clearing:done-nodes:${done.plan_id}`,
        seedFn: fnv1a,
        isTestMode: isTestMode.value && isWebDriver,
      })
    }
  }

  const labelNodes = useLabelNodes({
    isTestMode: () => isTestMode.value,
    getLabelsLod: () => labelsLod.value,
    getSnapshot: () => state.snapshot,
    getSelectedNodeId: () => state.selectedNodeId,
    getLayoutLinks: () => layout.links,
    getLayoutNodeById: (id) => getLayoutNodeById(id),
    getNodeById,
    getCameraZoom: () => camera.zoom,
    sizeForNode: (n) => sizeForNode(n),
    fxColorForNode,
  }).labelNodes

  const pickingAndHover = useAppPickingAndHover({
    hostEl,
    canvasEl,
    getLayoutNodes: () => layout.nodes,
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
  const edgeHover = pickingAndHover.edgeHover

  const dragToPinWiring = useAppDragToPinWiring({
    dragPreviewEl,
    isTestMode: () => isTestMode.value,
    pickNodeAt,
    getLayoutNodeById: (id) => layoutIndex.value.nodeById.get(id) ?? null,
    setSelectedNodeId: selectNode,
    clearHoveredEdge,
    clientToScreen,
    screenToWorld,
    canvasEl,
    renderOnce,
    physics,
    pinnedPos,
    getNodeById,
    getCamera: () => camera,
  })

  const dragToPin = dragToPinWiring.dragToPin
  const hideDragPreview = dragToPinWiring.hideDragPreview

  const canvasInteractionsWiring = useAppCanvasInteractionsWiring({
    isTestMode: () => isTestMode.value,
    pickNodeAt,
    selectNode,
    setNodeCardOpen,
    clearHoveredEdge,
    dragToPin,
    cameraSystem,
    edgeHover,
    getPanActive: () => panState.active,
  })

  async function loadSnapshotForUi(eq: string): Promise<{ snapshot: GraphSnapshot; sourcePath: string }> {
    if (!isRealMode.value) return loadSnapshotFixtures(eq)

    const runId = real.runId
    const scenarioId = real.selectedScenarioId

    const runState = String(real.runStatus?.state ?? '').toLowerCase()
    const isActiveRun =
      !!runId &&
      // Optimistic: if runId exists but status not fetched yet, assume active to avoid falling back to preview.
      (!real.runStatus || runState === 'running' || runState === 'paused' || runState === 'created' || runState === 'stopping')

    // Real mode: if we have a run, use run snapshot
    if (isActiveRun) {
      if (!real.accessToken) throw new Error('Missing access token')
      const snapshot = await getSnapshot({ apiBase: real.apiBase, accessToken: real.accessToken }, runId, eq)
      // Real snapshots currently don't declare FX limits; apply a sane default cap so long sessions
      // can't accumulate too many particles (sparks + pulses + bursts).
      if (!snapshot.limits) snapshot.limits = { max_particles: 220 }
      return { snapshot, sourcePath: `GET ${real.apiBase}/simulator/runs/${encodeURIComponent(runId)}/graph/snapshot` }
    }

    // Real mode: no run, but have scenario selected - show preview
    // Use desiredMode so UI can toggle between sandbox(topology-only) and real(DB-enriched) previews.
    if (scenarioId && real.accessToken) {
      try {
        const snapshot = await getScenarioPreview({ apiBase: real.apiBase, accessToken: real.accessToken }, scenarioId, eq, {
          mode: real.desiredMode,
        })
        if (!snapshot.limits) snapshot.limits = { max_particles: 220 }
        return { snapshot, sourcePath: `GET ${real.apiBase}/simulator/scenarios/${encodeURIComponent(scenarioId)}/graph/preview` }
      } catch (e) {
        // Fallback to empty if preview fails
        console.warn('Failed to load scenario preview:', e)
      }
    }

    // Fallback: empty snapshot
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

  async function loadEventsForUi(eq: string, playlist: string) {
    // Real mode uses live SSE, not demo playlists.
    if (isRealMode.value) return { events: [], sourcePath: `SSE ${real.apiBase}/simulator/runs/{run_id}/events` }
    return loadEventsFixtures(eq, playlist)
  }

  const sceneAndDemo = useAppSceneAndDemo({
    eq,
    scene,
    layoutMode,
    effectiveEq,
    state,
    isTestMode: () => isTestMode.value,
    isDev: () => import.meta.env.DEV,
    isWebDriver: () => isWebDriver,
    isEqAllowed: (v) => ALLOWED_EQS.has(String(v ?? '').toUpperCase()),
    loadSnapshot: loadSnapshotForUi,
    loadEvents: loadEventsForUi,
    assertPlaylistEdgesExistInSnapshot,
    clearScheduledTimeouts,
    resetOverlays,
    resetCamera,
    resetLayoutKeyCache: () => layoutCoordinator.resetLayoutKeyCache(),
    resizeAndLayout,
    ensureRenderLoop,
    setupResizeListener: () => layoutCoordinator.setupResizeListener(),
    teardownResizeListener: () => layoutCoordinator.teardownResizeListener(),
    stopRenderLoop,
    demoPlayer,
    setSelectedNodeId: selectNode,
    fxState,
  })

  const sceneState = sceneAndDemo.sceneState

  const clearingPlansById = new Map<string, ClearingPlanEvent>()

  function cleanupRealRunFxAndTimers() {
    clearScheduledTimeouts()
    resetOverlays()
    clearHoveredEdge()
    clearingPlanFxStarted.clear()
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
    realPatchApplier,
    signedBalanceForNodeId,
    pushBalanceDeltaLabels,
    runRealTxFx,
    runRealClearingPlanFx,
    runRealClearingDoneFx,
    clearingPlansById,
  })

  useAppLifecycle({
    layoutMode,
    resizeAndLayout,
    persistedPrefs,
    setupDevHook: sceneAndDemo.setupDevHook,
    sceneState,
    hideDragPreview,
    physics,
    getLayoutSize: () => ({ w: layout.w, h: layout.h }),
  })

  return {
    apiMode,

    // flags
    isDemoFixtures,
    isTestMode,
    isWebDriver,

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
      setDesiredMode: (v: SimulatorMode) => {
        real.desiredMode = v
      },
      setIntensityPercent: (v: number) => {
        real.intensityPercent = Math.max(0, Math.min(100, Math.round(v)))
      },
      refreshScenarios: realMode.refreshScenarios,
      startRun: realMode.startRun,
      pause: realMode.pause,
      resume: realMode.resume,
      stop: realMode.stop,
      applyIntensity: realMode.applyIntensity,
      refreshSnapshot: realMode.refreshSnapshot,
      refreshArtifacts: realMode.refreshArtifacts,
      downloadArtifact: realMode.downloadArtifact,
    },

    // state + prefs
    state,
    eq,
    scene,
    layoutMode,
    quality,
    labelsLod,

    // refs
    hostEl,
    canvasEl,
    fxCanvasEl,
    dragPreviewEl,

    // derived ui
    overlayLabelScale,
    showDemoControls,
    showResetView,

    // selection + overlays
    isNodeCardOpen,
    hoveredEdge,
    clearHoveredEdge,
    edgeTooltipStyle: pickingAndHover.edgeTooltipStyle,
    selectedNode: viewWiring.selectedNode,
    nodeCardStyle: viewWiring.nodeCardStyle,
    selectedNodeEdgeStats,

    // pinning
    dragToPin,
    isSelectedPinned,
    pinSelectedNode,
    unpinSelectedNode,

    // handlers
    onCanvasClick: canvasInteractionsWiring.onCanvasClick,
    onCanvasDblClick: canvasInteractionsWiring.onCanvasDblClick,
    onCanvasPointerDown: canvasInteractionsWiring.onCanvasPointerDown,
    onCanvasPointerMove: canvasInteractionsWiring.onCanvasPointerMove,
    onCanvasPointerUp: canvasInteractionsWiring.onCanvasPointerUp,
    onCanvasWheel: canvasInteractionsWiring.onCanvasWheel,

    // demo
    playlist,
    canDemoPlay: sceneAndDemo.canDemoPlay,
    demoPlayLabel: sceneAndDemo.demoPlayLabel,
    runTxOnce: sceneAndDemo.runTxOnce,
    runClearingOnce: sceneAndDemo.runClearingOnce,
    demoStepOnce: sceneAndDemo.demoStepOnce,
    demoTogglePlay: sceneAndDemo.demoTogglePlay,
    demoReset: sceneAndDemo.demoReset,

    // labels
    labelNodes,
    floatingLabelsViewFx,
    worldToCssTranslateNoScale: viewWiring.worldToCssTranslateNoScale,

    // helpers for template
    getNodeById,
    resetView: viewWiring.resetView,
  }
}
