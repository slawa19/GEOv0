import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { loadSnapshot as loadSnapshotFixtures } from '../fixtures'
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

import { getActiveRun, getSnapshot, getScenarioPreview } from '../api/simulatorApi'
import { actionClearingOnce, actionTxOnce } from '../api/simulatorApi'
import type { ArtifactIndexItem, RunStatus, ScenarioSummary, SimulatorMode } from '../api/simulatorTypes'
import { normalizeApiBase } from '../api/apiBase'

import { useAppLifecycle } from './useAppLifecycle'
import { useAppUiDerivedState } from './useAppUiDerivedState'
import { useLabelNodes } from './useLabelNodes'
import { useLayoutIndex } from './useLayoutIndex'
import { usePersistedSimulatorPrefs } from './usePersistedSimulatorPrefs'
import { useSelectedNodeEdgeStats } from './useSelectedNodeEdgeStats'
import { useSnapshotIndex } from './useSnapshotIndex'
import { useNodeSelectionAndCardOpen } from './useNodeSelectionAndCardOpen'
import { useAppPickingAndHover } from './useAppPickingAndHover'
import { useAppFxAndRender } from './useAppFxAndRender'
import { useAppPhysicsAndPinningWiring } from './useAppPhysicsAndPinningWiring'
import { useAppLayoutWiring } from './useAppLayoutWiring'
import { useAppDragToPinWiring } from './useAppDragToPinWiring'
import { useAppCanvasInteractionsWiring } from './useAppCanvasInteractionsWiring'
import { useAppViewWiring } from './useAppViewWiring'
import { useSimulatorRealMode, type RealModeState } from './useSimulatorRealMode'
import { useAppSceneState } from './useAppSceneState'
import { useGeoSimDevHookSetup } from './useGeoSimDevHookSetup'
import { getFxConfig, intensityScale } from '../config/fxConfig'
import { createSimulatorIsAnimating } from './simulatorIsAnimating'
import { createInteractionHold } from './interactionHold'
import { createDemoActivityHold } from './demoActivityHold'

export function useSimulatorApp() {
  const eq = ref('UAH')
  const scene = ref<SceneId>('A')

  const layoutMode = ref<LayoutMode>('admin-force')

  const isDemoFixtures = computed(() => String(import.meta.env.VITE_DEMO_FIXTURES ?? '1') === '1')
  const isTestMode = computed(() => String(import.meta.env.VITE_TEST_MODE ?? '0') === '1')

  // Playwright sets navigator.webdriver=true.
  // Use it to keep screenshot tests stable even if someone runs the dev server with VITE_TEST_MODE=1.
  const isWebDriver = typeof navigator !== 'undefined' && (navigator as any).webdriver === true

  const isE2eScreenshots = computed(() => isTestMode.value && isWebDriver)

  const apiMode = computed<'fixtures' | 'real'>(() => {
    // E2E screenshot tests must be fully offline/deterministic.
    // Force fixtures pipeline regardless of a developer's local `.env.local` (which may enable real mode).
    if (isE2eScreenshots.value) return 'fixtures'
    try {
      const p = new URLSearchParams(window.location.search).get('mode')?.toLowerCase()
      if (p === 'real') return 'real'
      if (p === 'fixtures') return 'fixtures'
      // Backward-compat: legacy param value.
      if (p === 'demo') return 'fixtures'
    } catch {
      // ignore
    }
    const env = String(import.meta.env.VITE_API_MODE ?? '').trim().toLowerCase()
    if (env === 'real') return 'real'
    if (env === 'fixtures') return 'fixtures'
    if (env === 'demo') return 'fixtures'
    // Default to fixtures so UI can run without backend by default.
    return 'fixtures'
  })

  const isRealMode = computed(() => apiMode.value === 'real')

  const isLocalhost =
    typeof window !== 'undefined' && ['localhost', '127.0.0.1', '::1'].includes(window.location.hostname)

  const isFxDebugEnabled = computed(() => {
    try {
      const sp = new URLSearchParams(window.location.search)

      // Demo UI implies FX Debug should be available (backend-driven, canonical SSE pipeline).
      const ui = String(sp.get('ui') ?? '').toLowerCase()
      if (ui === 'demo') return true

      const p = sp.get('debug')
      return p === '1' || String(p || '').toLowerCase() === 'true'
    } catch {
      return false
    }
  })

  const isDemoUi = computed(() => {
    try {
      const ui = String(new URLSearchParams(window.location.search).get('ui') ?? '').toLowerCase()
      return ui === 'demo'
    } catch {
      return false
    }
  })

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
    snapshot: null,
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
    intensityPercent: (() => {
      const n = Number(lsGet('geo.sim.v2.intensityPercent', '30'))
      return Number.isFinite(n) ? n : 30
    })(),
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

  // Interaction Quality: short hold window after user input to render without blur-heavy paths.
  const interactionHold = createInteractionHold({ holdMs: 250 })

  // Demo UI: keep render loop awake around sporadic SSE/debug actions.
  const demoHold = createDemoActivityHold({ holdMs: 1200 })
  function markDemoActivity() {
    if (!isDemoUi.value) return
    demoHold.markDemoEvent()
  }

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
    wakeUp('animation')
  }

  async function e2eClearingOnce(): Promise<void> {
    if (!isE2eScreenshots.value) return
    // Offline deterministic clearing step.
    // Use active nodes (clearing glow in baseGraph) instead of active edges (tx color policy).
    const snap = state.snapshot
    if (!snap || snap.nodes.length === 0) return
    const ids = snap.nodes.slice(0, 4).map((n) => String(n.id))
    for (const id of ids) addActiveNode(id, 1500)
    wakeUp('animation')
  }

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
  // Baseline default. Persisted prefs (if any) will override this on mount.
  // For Playwright screenshot tests we must use the exact visuals the snapshots were recorded with.
  // (In particular: gradients + full glow sprites are enabled only on `high`.)
  const quality = ref<Quality>(isE2eScreenshots.value ? 'high' : 'med')

  // If Chrome struggles even right after opening the page (e.g. ~1 FPS),
  // auto-downgrade quality to recover responsiveness.
  // This is deliberately conservative: only kicks in on very low measured FPS.
  onMounted(() => {
    if (isTestMode.value) return
    if (isWebDriver) return

    let frames = 0
    let startMs = 0
    let rafId = 0
    let guardActive = true
    let qualityTouchedWhileGuardActive = false

    const stopWatch = watch(
      quality,
      () => {
        if (guardActive) qualityTouchedWhileGuardActive = true
      },
      { flush: 'sync' },
    )

    const loop = (t: number) => {
      if (startMs === 0) startMs = t
      frames++

      const elapsed = t - startMs
      if (elapsed < 1800) {
        rafId = window.requestAnimationFrame(loop)
        return
      }

      guardActive = false
      stopWatch()
      window.cancelAnimationFrame(rafId)

      const fps = (frames * 1000) / Math.max(1, elapsed)

      // Emergency recovery: if we're below ~12 FPS on the landing view,
      // force low quality to make the UI usable.
      if (!qualityTouchedWhileGuardActive && fps < 12 && quality.value !== 'low') {
        quality.value = 'low'
      }
    }

    rafId = window.requestAnimationFrame(loop)
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
  const dprClamp = uiDerived.dprClamp
  const showResetView = uiDerived.showResetView
  const overlayLabelScale = uiDerived.overlayLabelScale

  const snapshotRef = computed(() => state.snapshot)

  // `fxAndRender` is initialized later (it needs layout + camera), but layout/resize
  // code may want to wake the render loop. IMPORTANT: this must be a stable wrapper.
  // Some wirings capture the function reference at init-time; reassigning a `let wakeUp = ...`
  // later would not update the captured reference.
  let wakeUpImpl: (source?: 'user' | 'animation') => void = () => undefined
  const wakeUp = (source?: 'user' | 'animation') => wakeUpImpl(source)

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

  function syncLayoutFromSnapshot(snapshot: GraphSnapshot) {
    // Layout nodes/links are copies (see forceLayout.ts spreads base objects).
    // When the scene loader decides an update is incremental (same node IDs), it may skip relayout.
    // In that case we must sync viz fields into the existing layout objects used for rendering.
    const snapNodeById = new Map(snapshot.nodes.map((n) => [n.id, n] as const))
    for (const n of layout.nodes as any[]) {
      const src = snapNodeById.get(String(n.id))
      if (!src) continue
      n.name = src.name
      n.type = src.type
      n.status = src.status
      n.links_count = src.links_count
      n.net_balance_atoms = src.net_balance_atoms
      n.net_sign = src.net_sign
      n.net_balance = src.net_balance
      n.viz_color_key = src.viz_color_key
      n.viz_shape_key = src.viz_shape_key
      n.viz_size = src.viz_size
      n.viz_badge_key = src.viz_badge_key
    }

    const snapLinkByKey = new Map(snapshot.links.map((l) => [keyEdge(l.source, l.target), l] as const))
    for (const l of layout.links as any[]) {
      const k = String(l.__key ?? keyEdge(String(l.source), String(l.target)))
      const src = snapLinkByKey.get(k)
      if (!src) continue
      l.trust_limit = src.trust_limit
      l.used = src.used
      l.available = src.available
      l.status = src.status
      l.viz_color_key = src.viz_color_key
      l.viz_width_key = src.viz_width_key
      l.viz_alpha_key = src.viz_alpha_key
    }

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

  function detectGpuAccelerationLikelyAvailable(): boolean {
    if (typeof document === 'undefined') return true
    try {
      const canvas = document.createElement('canvas')
      const gl = (canvas.getContext('webgl') || canvas.getContext('experimental-webgl')) as WebGLRenderingContext | null
      if (!gl) return false

      const dbg = gl.getExtension('WEBGL_debug_renderer_info') as any
      if (dbg) {
        const renderer = String(gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) ?? '')
        const r = renderer.toLowerCase()
        if (r.includes('microsoft basic render driver')) return false
        if (r.includes('swiftshader')) return false
        if (r.includes('llvmpipe')) return false
      }

      // If WebGL context exists, assume some form of GPU path is available.
      return true
    } catch {
      return true
    }
  }

  const gpuAccelLikely = ref(true)
  onMounted(() => {
    // Keep deterministic in tests and avoid fighting the user in webdriver.
    if (isTestMode.value) return
    if (isWebDriver) return

    gpuAccelLikely.value = detectGpuAccelerationLikelyAvailable()

    // If the browser is in software-only mode, prefer low quality by default.
    // Do not override if the user touched quality shortly after mount.
    if (!gpuAccelLikely.value) {
      let touched = false
      const stop = watch(
        quality,
        () => {
          touched = true
        },
        { flush: 'sync' },
      )

      window.setTimeout(() => {
        stop()
        if (!touched && quality.value !== 'low') {
          quality.value = 'low'
        }
      }, 400)
    }
  })

  const viewWiring = useAppViewWiring({
    canvasEl,
    hostEl,
    getLayoutNodes: () => layout.nodes,
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
    // Use 'focus' LOD during drag OR while physics is settling — both enable dragMode fast-path in baseGraph.
    getLinkLod: () => {
      if (dragToPin.dragState.active && dragToPin.dragState.dragging) return 'focus'
      if (typeof (physics as any).isRunning === 'function' && (physics as any).isRunning()) return 'focus'
      return 'full'
    },
    getHiddenNodeId: () => (dragToPin.dragState.active && dragToPin.dragState.dragging ? dragToPin.dragState.nodeId : null),
    beforeDraw: () => {
      if (physics.isRunning()) {
        physics.tickAndSyncToLayout()
      }
    },
    isAnimating: createSimulatorIsAnimating({
      isPhysicsRunning: () => physics.isRunning(),
      isDemoHoldActive: () => isDemoUi.value && demoHold.isWithinHoldWindow(),
    }),
    isInteracting: () => interactionHold.isInteracting.value,
    getInteractionIntensity: () => interactionHold.getIntensity(),
  })

  const fxState = fxAndRender.fxState
  const hoveredEdge = fxAndRender.hoveredEdge
  const clearHoveredEdge = fxAndRender.clearHoveredEdge
  const activeEdges = fxAndRender.activeEdges
  const addActiveEdge = fxAndRender.addActiveEdge
  const pruneActiveEdges = fxAndRender.pruneActiveEdges
  const activeNodes = fxAndRender.activeNodes
  const addActiveNode = fxAndRender.addActiveNode
  const pruneActiveNodes = fxAndRender.pruneActiveNodes
  const pushFloatingLabel = fxAndRender.pushFloatingLabel
  const resetOverlays = fxAndRender.resetOverlays
  const floatingLabelsViewFx = fxAndRender.floatingLabelsViewFx
  const scheduleTimeout = fxAndRender.scheduleTimeout
  const clearScheduledTimeouts = fxAndRender.clearScheduledTimeouts
  const ensureRenderLoop = fxAndRender.ensureRenderLoop
  const stopRenderLoop = fxAndRender.stopRenderLoop
  const renderOnce = fxAndRender.renderOnce
  wakeUpImpl = fxAndRender.wakeUp

  const showPerfOverlay = computed(() => {
    if (isTestMode.value) return false
    if (typeof window === 'undefined') return false
    try {
      return new URLSearchParams(window.location.search).get('perf') === '1'
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
      const fx = fxState as any
      perf.lastFps = typeof fx?.__lastFps === 'number' ? fx.__lastFps : null
      perf.fxBudgetScale = typeof fx?.__fxBudgetScale === 'number' ? fx.__fxBudgetScale : null
      perf.maxParticles = typeof fx?.__maxParticles === 'number' ? fx.__maxParticles : null
      perf.renderQuality = typeof fx?.__renderQuality === 'string' ? fx.__renderQuality : null
      perf.dprClamp = typeof fx?.__dprClamp === 'number' ? fx.__dprClamp : null

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
  const pinSelectedNode = physicsAndPinning.pinSelectedNode
  const unpinSelectedNode = physicsAndPinning.unpinSelectedNode

  layoutWiring.initComputeLayout({
    pinning,
    physics,
  })

  // Real-mode TX visuals must be independent from any offline/demo playback.
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

  function clampRealTxTtlMs(ttlRaw: unknown, fallbackMs = 1200): number {
    const ttlN = Number(ttlRaw ?? fallbackMs)
    const ttl = Number.isFinite(ttlN) ? ttlN : fallbackMs
    return Math.max(REAL_TX_FX.ttlMinMs, Math.min(REAL_TX_FX.ttlMaxMs, ttl))
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

    const ttlMs = clampRealTxTtlMs(evt.ttl_ms)
    const k = intensityScale(evt.intensity_key)

    // NOTE: We no longer add activeEdges for TX sparks — the beam itself provides
    // sufficient visual feedback. ActiveEdges created a "stuck line" effect when
    // combined with the shrinking beam trail.
    // For clearing, activeEdges are still used to highlight the cycle path.

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

  const REAL_CLEARING_FX = getFxConfig('real')

  const clearingPlanFxStarted = new Set<string>()

  function pushFloatingLabelWhenReady(
    opts: Parameters<typeof pushFloatingLabel>[0],
    retryLeft = 6,
    retryDelayMs = 90,
  ) {
    // Overlay state is now tolerant to nodes missing from layout (it won't drop labels).
    // Keep the retry mechanism to avoid “label appears later but immediately expires” in edge cases,
    // but if we run out of retries, still push once so it can render when the node shows up.
    if (getLayoutNodeById(opts.nodeId) || retryLeft <= 0) {
      pushFloatingLabel(opts)
      return
    }

    scheduleTimeout(() => pushFloatingLabelWhenReady(opts, retryLeft - 1, retryDelayMs), retryDelayMs)
  }


  function pushTxAmountLabel(
    nodeId: string,
    signedAmount: string,
    unit: string,
    opts?: { throttleMs?: number },
  ) {
    const id = String(nodeId ?? '').trim()
    if (!id) return

    const raw = String(signedAmount ?? '').trim()
    if (!raw) return

    const sign = raw.startsWith('-') ? '-' : '+'
    const amountText = raw.replace(/^\+/, '')
    const color = sign === '+' ? VIZ_MAPPING.fx.tx_spark.trail : VIZ_MAPPING.fx.clearing_debt

    pushFloatingLabelWhenReady({
      nodeId: id,
      text: `${sign}${amountText.replace(/^-/, '')} ${unit}`,
      color: fxColorForNode(id, color),
      ttlMs: 1900,
      offsetYPx: -6,
      throttleKey: `amt:${id}`,
      throttleMs: opts?.throttleMs ?? 240,
    })
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
    // REMOVED: No flash at plan start — only flash once at clearing.done

    // Collect all edges from plan for highlighting (even without steps).
    const allPlanEdges: Array<{ from: string; to: string }> = []
    for (const step of plan.steps ?? []) {
      for (const e of step.highlight_edges ?? []) allPlanEdges.push({ from: e.from, to: e.to })
      for (const e of step.particles_edges ?? []) allPlanEdges.push({ from: e.from, to: e.to })
    }

    // Fallback: if plan has cycle_edges directly (some backends), use those.
    const cycleEdges = (plan as any).cycle_edges as Array<{ from: string; to: string }> | undefined
    if (cycleEdges && cycleEdges.length > 0) {
      for (const e of cycleEdges) allPlanEdges.push({ from: e.from, to: e.to })
    }

    const maxAt = Math.max(
      0,
      ...((plan.steps ?? [])
        .map((s) => Number(s.at_ms ?? 0))
        .filter((v) => Number.isFinite(v))),
    )
    const planFxTtlMs = Math.max(1500, Math.min(30_000, maxAt + 2500))

    // Clearing Viz v2: highlight all nodes in the cycle during the plan phase.
    if (allPlanEdges.length > 0) {
      const nodeIds = nodesFromEdges(allPlanEdges)
      for (const id of nodeIds) addActiveNode(id, planFxTtlMs)
    }

    // Highlight all clearing edges immediately (so they're visible during the plan phase).
    if (allPlanEdges.length > 0) {
      const nowMs = typeof performance !== 'undefined' ? performance.now() : Date.now()
      for (const e of allPlanEdges) addActiveEdge(keyEdge(e.from, e.to), REAL_CLEARING_FX.highlightPulseMs + 3000)

      spawnEdgePulses(fxState, {
        edges: allPlanEdges,
        nowMs,
        durationMs: REAL_CLEARING_FX.highlightPulseMs,
        color: clearingColor,
        thickness: REAL_CLEARING_FX.highlightThickness,
        seedPrefix: `real-clearing:plan:${planId}:${plan.equivalent}`,
        countPerEdge: 1,
        keyEdge,
        seedFn: fnv1a,
        isTestMode: isTestMode.value && isWebDriver,
      })
    }

    // Process steps for additional FX (micro-transactions within clearing).
    for (const step of plan.steps ?? []) {
      const atMs = Math.max(0, Number(step.at_ms ?? 0))
      scheduleTimeout(() => {
        const nowMs = typeof performance !== 'undefined' ? performance.now() : Date.now()
        const budgetScaleRaw = (fxState as any).__fxBudgetScale
        const budgetScale =
          typeof budgetScaleRaw === 'number' && Number.isFinite(budgetScaleRaw) ? Math.max(0.25, Math.min(1, budgetScaleRaw)) : 1

        const burstNodeCap = budgetScale >= 0.85 ? 30 : budgetScale >= 0.7 ? 16 : 8

        const k = intensityScale(step.intensity_key)

        const particles = (step.particles_edges ?? []).map((e) => ({ from: e.from, to: e.to }))
        if (particles.length > 0) {
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

          if (budgetScale >= 0.55) {
            const nodeIds = nodesFromEdges(particles)
            const capped = nodeIds.length > burstNodeCap ? nodeIds.slice(0, burstNodeCap) : nodeIds
            spawnNodeBursts(fxState, {
              nodeIds: capped,
              nowMs,
              durationMs: REAL_CLEARING_FX.nodeBurstMs,
              color: clearingColor,
              kind: 'clearing',
              seedPrefix: `real-clearing:nodes:${planId}:${step.at_ms}`,
              seedFn: fnv1a,
              isTestMode: isTestMode.value && isWebDriver,
            })
          }
        }
        // REMOVED: No flash per step
      }, atMs)
    }

    scheduleTimeout(() => {
      clearingPlanFxStarted.delete(planId)
    }, planFxTtlMs)
  }

  function runRealClearingDoneFx(plan: ClearingPlanEvent | undefined, done: ClearingDoneEvent) {
    const nowMs = typeof performance !== 'undefined' ? performance.now() : Date.now()
    const clearingColor = VIZ_MAPPING.fx.clearing_debt

    const budgetScaleRaw = (fxState as any).__fxBudgetScale
    const budgetScale =
      typeof budgetScaleRaw === 'number' && Number.isFinite(budgetScaleRaw) ? Math.max(0.25, Math.min(1, budgetScaleRaw)) : 1
    const burstNodeCap = budgetScale >= 0.85 ? 30 : budgetScale >= 0.7 ? 16 : 8

    // Single flash at clearing completion (the only flash for entire clearing cycle).
    state.flash = 0.85

    // Collect all edges from plan (for edge highlighting).
    const planEdges: Array<{ from: string; to: string }> = []
    if (plan?.steps) {
      for (const s of plan.steps) {
        for (const e of s.highlight_edges ?? []) planEdges.push({ from: e.from, to: e.to })
        for (const e of s.particles_edges ?? []) planEdges.push({ from: e.from, to: e.to })
      }
    }

    // Fallback: use cycle_edges from plan or done if steps are empty.
    const cycleEdges = (plan as any)?.cycle_edges ?? (done as any)?.cycle_edges
    if (planEdges.length === 0 && Array.isArray(cycleEdges)) {
      for (const e of cycleEdges) planEdges.push({ from: e.from, to: e.to })
    }

    // Fallback: derive edges from done.node_patch if still empty (edge between consecutive nodes).
    if (planEdges.length === 0 && done.node_patch && done.node_patch.length >= 2) {
      const patchIds = done.node_patch.map((p) => p.id).filter(Boolean)
      for (let i = 0; i < patchIds.length; i++) {
        const from = patchIds[i]!
        const to = patchIds[(i + 1) % patchIds.length]!
        if (from && to) planEdges.push({ from, to })
      }
    }

    const nodeIds = nodesFromEdges(planEdges)

    // Keep cycle nodes visible during completion flash.
    if (nodeIds.length > 0) {
      for (const id of nodeIds) addActiveNode(id, 5200)
    }

    // Highlight edges with pulses.
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

      if (budgetScale >= 0.55) {
        const capped = nodeIds.length > burstNodeCap ? nodeIds.slice(0, burstNodeCap) : nodeIds
        spawnNodeBursts(fxState, {
          nodeIds: capped,
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

    // Show total cleared amount as a single floating label in the center of the cycle.
    // This replaces multiple per-node labels for a cleaner UX.
    if (nodeIds.length > 0) {
      // Compute center of cycle (average of all node positions).
      let sumX = 0
      let sumY = 0
      let count = 0
      let centerNodeId = nodeIds[0]!
      for (const id of nodeIds) {
        const ln = getLayoutNodeById(id)
        if (ln && typeof ln.__x === 'number' && typeof ln.__y === 'number') {
          sumX += ln.__x
          sumY += ln.__y
          count++
        }
      }

      // Pick the node closest to center to anchor the label.
      if (count > 0) {
        const cx = sumX / count
        const cy = sumY / count
        let minDist = Infinity
        for (const id of nodeIds) {
          const ln = getLayoutNodeById(id)
          if (ln && typeof ln.__x === 'number' && typeof ln.__y === 'number') {
            const d = Math.hypot(ln.__x - cx, ln.__y - cy)
            if (d < minDist) {
              minDist = d
              centerNodeId = id
            }
          }
        }
      }

      const clearedAmount = String(done.cleared_amount ?? '').trim()
      if (clearedAmount && clearedAmount !== '0' && clearedAmount !== '0.0' && clearedAmount !== '0.00') {
        pushFloatingLabelWhenReady({
          nodeId: centerNodeId,
          text: `🔄 −${clearedAmount.replace(/^-/, '')} ${done.equivalent}`,
          color: clearingColor,
          ttlMs: 3200,
          offsetYPx: -18,
          throttleKey: `clearing-total:${done.plan_id}`,
          throttleMs: 500,
        })
      }
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
    wakeUp,
    markInteraction: (markOpts) => interactionHold.markInteraction(markOpts),
    pickNodeAt,
    selectNode,
    setNodeCardOpen,
    clearHoveredEdge,
    dragToPin,
    cameraSystem,
    edgeHover,
    getPanActive: () => panState.active,
  })

  onUnmounted(() => {
    interactionHold.dispose()
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

  const sceneState = useAppSceneState({
    eq,
    scene,
    layoutMode,
    effectiveEq,
    state,
    isTestMode: () => isTestMode.value,
    isEqAllowed: (v) => ALLOWED_EQS.has(String(v ?? '').toUpperCase()),
    loadSnapshot: loadSnapshotForUi,
    onIncrementalSnapshotLoaded: (snapshot) => syncLayoutFromSnapshot(snapshot),
    clearScheduledTimeouts,
    resetCamera,
    resetLayoutKeyCache: () => layoutCoordinator.resetLayoutKeyCache(),
    resetOverlays,
    resizeAndLayout,
    ensureRenderLoop,
    setupResizeListener: () => layoutCoordinator.setupResizeListener(),
    teardownResizeListener: () => layoutCoordinator.teardownResizeListener(),
    stopRenderLoop,
  })

  const clearingPlansById = new Map<string, ClearingPlanEvent>()

  function cleanupRealRunFxAndTimers() {
    // Keep critical timers (e.g. “arrival” labels) so quick stop/restart/cleanup doesn't silently drop them.
    // Each critical timer must self-guard (runId / sseSeq / abort signal) before emitting UI.
    clearScheduledTimeouts({ keepCritical: true })
    resetOverlays()
    clearHoveredEdge()
    clearingPlanFxStarted.clear()

    // Reset token-bucket between runs so a newly started run can always show TX FX immediately.
    txFxTokens = REAL_TX_FX.burst
    txFxLastRefillAtMs = typeof performance !== 'undefined' ? performance.now() : Date.now()
    txFxLastSpawnAtMs = 0

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
    pushTxAmountLabel,
    runRealTxFx,
    clampRealTxTtlMs,
    runRealClearingPlanFx,
    runRealClearingDoneFx,
    clearingPlansById,
    scheduleTimeout,
    wakeUp,
    onAnySseEvent: markDemoActivity,
  })

  const fxDebugBusy = ref(false)

  async function ensureRunForFxDebug(): Promise<string> {
    if (real.runId) {
      // If this is an auto-started FX debug fixtures run, keep it paused so heartbeat doesn't spam events.
      try {
        const isFxDebugRun = localStorage.getItem('geo.sim.v2.fxDebugRun') === '1'
        if (isFxDebugRun) {
          if (!real.runStatus) await realMode.refreshRunStatus()
          const st = String(real.runStatus?.state ?? '').toLowerCase()
          const shouldPause = !!st && st !== 'paused' && st !== 'stopped' && st !== 'error'
          if (shouldPause) {
            await realMode.pause()
          }
        }
      } catch {
        // ignore
      }
      return real.runId
    }

    if (!String(real.accessToken ?? '').trim()) {
      throw new Error('Missing access token')
    }

    // FX Debug may be clicked before scenarios finish loading.
    // Ensure a valid scenario selection exists before attempting to start a run.
    if (!String(real.selectedScenarioId ?? '').trim()) {
      await realMode.refreshScenarios()
    }
    if (!String(real.selectedScenarioId ?? '').trim()) {
      const detail = String(real.lastError ?? '').trim()
      throw new Error(detail ? `Failed to select scenario for FX debug: ${detail}` : 'No scenario selected for FX debug')
    }

    try {
      localStorage.setItem('geo.sim.v2.fxDebugRun', '1')
    } catch {
      // ignore
    }

    // Autostart a paused fixtures run for fast FX debugging.
    // Important: keep it paused so backend heartbeat doesn't flood tx.updated events.
    // Use real-mode so the graph stays DB-enriched (fixtures mode is topology-only).
    await realMode.startRun({ mode: 'real', pauseImmediately: true })

    // If a run is already active server-side (e.g. another tab), the backend may reject
    // creating a new one (SIMULATOR_MAX_ACTIVE_RUNS). In that case, attach to the active run.
    if (!real.runId) {
      const msg = String(real.lastError ?? '')
      const looksLikeConflict = msg.includes('HTTP 409') || msg.includes(' 409 ') || msg.toLowerCase().includes('conflict')
      if (looksLikeConflict) {
        try {
          const active = await getActiveRun({ apiBase: real.apiBase, accessToken: real.accessToken })
          const activeRunId = String(active?.run_id ?? '').trim()
          if (activeRunId) {
            real.runId = activeRunId
            await realMode.refreshRunStatus()
            // If we attached to a running fixtures run (likely from a previous FX debug attempt), pause it.
            try {
              const st = String(real.runStatus?.state ?? '').toLowerCase()
              const shouldPause = !!st && st !== 'paused' && st !== 'stopped' && st !== 'error'
              if (shouldPause) {
                await realMode.pause()
              }
            } catch {
              // ignore
            }
            await realMode.refreshSnapshot()
            return real.runId
          }
        } catch {
          // fall through to error
        }
      }
    }

    if (!real.runId) {
      const detail = String(real.lastError ?? '').trim()
      throw new Error(detail ? `Failed to start run for FX debug: ${detail}` : 'Failed to start run for FX debug')
    }
    return real.runId
  }

  async function fxDebugTxOnce(): Promise<void> {
    if (!isFxDebugEnabled.value) return
    if (!real.accessToken) throw new Error('Missing access token')
    markDemoActivity()
    fxDebugBusy.value = true
    try {
      const runId = await ensureRunForFxDebug()
      await actionTxOnce({ apiBase: real.apiBase, accessToken: real.accessToken }, runId, {
        equivalent: effectiveEq.value,
        seed: `ui-fx-debug-tx:${Date.now()}`,
        client_action_id: `ui-fx-debug-tx:${Date.now()}`,
      })
    } finally {
      fxDebugBusy.value = false
    }
  }

  async function fxDebugClearingOnce(): Promise<void> {
    if (!isFxDebugEnabled.value) return
    if (!real.accessToken) throw new Error('Missing access token')
    markDemoActivity()
    fxDebugBusy.value = true
    try {
      const runId = await ensureRunForFxDebug()
      await actionClearingOnce({ apiBase: real.apiBase, accessToken: real.accessToken }, runId, {
        equivalent: effectiveEq.value,
        seed: `ui-fx-debug-clearing:${Date.now()}`,
        client_action_id: `ui-fx-debug-clearing:${Date.now()}`,
      })
    } finally {
      fxDebugBusy.value = false
    }
  }

  const devHook = useGeoSimDevHookSetup({
    isDev: () => import.meta.env.DEV,
    isTestMode: () => isTestMode.value,
    isWebDriver: () => isWebDriver,
    getState: () => state,
    fxState,
    runTxOnce: fxDebugTxOnce,
    runClearingOnce: fxDebugClearingOnce,
  })

  useAppLifecycle({
    layoutMode,
    resizeAndLayout,
    persistedPrefs,
    setupDevHook: devHook.setupDevHook,
    sceneState,
    hideDragPreview,
    physics,
    getLayoutSize: () => ({ w: layout.w, h: layout.h }),
  })

  return {
    apiMode,

    // flags
    isDemoFixtures,
    isDemoUi,
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

    effectiveEq,

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

    fxDebug: {
      enabled: isFxDebugEnabled,
      busy: fxDebugBusy,
      runTxOnce: fxDebugTxOnce,
      runClearingOnce: fxDebugClearingOnce,
    },

    // E2E-only (offline) actions for screenshot tests
    e2e: {
      runTxOnce: e2eTxOnce,
      runClearingOnce: e2eClearingOnce,
    },

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

    // render loop control (for wakeups from external events)
    wakeUp,

    // labels
    labelNodes,
    floatingLabelsViewFx,
    worldToCssTranslateNoScale: viewWiring.worldToCssTranslateNoScale,

    // helpers for template
    getNodeById,
    resetView: viewWiring.resetView,
  }
}
