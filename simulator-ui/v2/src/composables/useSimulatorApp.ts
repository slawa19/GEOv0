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
import { createPatchApplier } from '../demo/patches'

import {
  artifactDownloadUrl,
  createRun,
  getRun,
  getSnapshot,
  listArtifacts,
  listScenarios,
  pauseRun,
  resumeRun,
  setIntensity,
  stopRun,
} from '../api/simulatorApi'
import type { ArtifactIndexItem, RunStatus, ScenarioSummary, SimulatorMode } from '../api/simulatorTypes'
import { connectSse } from '../api/sse'
import { normalizeSimulatorEvent } from '../api/normalizeSimulatorEvent'
import { ApiError, authHeaders } from '../api/http'
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

  const real = reactive({
    apiBase: normalizeApiBase(lsGet('geo.sim.v2.apiBase', String(import.meta.env.VITE_GEO_API_BASE ?? '/api/v1'))),
    accessToken: lsGet(
      'geo.sim.v2.accessToken',
      ENV_ACCESS_TOKEN || (import.meta.env.DEV ? DEFAULT_DEV_ACCESS_TOKEN : ''),
    ),
    loadingScenarios: false,
    scenarios: [] as ScenarioSummary[],
    selectedScenarioId: lsGet('geo.sim.v2.selectedScenarioId', ''),
    desiredMode: (lsGet('geo.sim.v2.desiredMode', 'real') === 'fixtures' ? 'fixtures' : 'real') as SimulatorMode,
    intensityPercent: Number(lsGet('geo.sim.v2.intensityPercent', '50')) || 50,
    runId: lsGet('geo.sim.v2.runId', '') || null,
    runStatus: null as RunStatus | null,
    sseState: 'idle',
    lastEventId: null as string | null,
    lastError: '',
    artifacts: [] as ArtifactIndexItem[],
    artifactsLoading: false,
  })

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
    if (!runId) throw new Error('No run started (set token, choose scenario, Start run)')
    if (!real.accessToken) throw new Error('Missing access token')

    const snapshot = await getSnapshot({ apiBase: real.apiBase, accessToken: real.accessToken }, runId, eq)
    return { snapshot, sourcePath: `GET ${real.apiBase}/simulator/runs/${encodeURIComponent(runId)}/graph/snapshot` }
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

  // -----------------
  // Real Mode: SSE loop
  // -----------------

  let sseAbort: AbortController | null = null
  let sseSeq = 0

  const clearingPlansById = new Map<string, ClearingPlanEvent>()

  function stopSse() {
    sseAbort?.abort()
    sseAbort = null
    real.sseState = 'idle'
  }

  async function refreshRunStatus() {
    if (!real.runId || !real.accessToken) return
    try {
      const st = await getRun({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId)
      real.runStatus = st
      real.intensityPercent = Math.round(Number(st.intensity_percent ?? real.intensityPercent))
      real.lastError = st.last_error ? `${st.last_error.code}: ${st.last_error.message}` : ''
    } catch (e: any) {
      // If the server restarted, the in-memory run registry is gone.
      // Clear stale run_id so the UI doesn't look "stuck" with a blank graph.
      if (e instanceof ApiError && e.status === 404) {
        real.runId = null
        real.runStatus = null
        real.lastEventId = null
        real.artifacts = []
        real.sseState = 'idle'
        real.lastError = 'Stored run not found (server restarted). Click Start run to create a new run.'
        return
      }
      real.lastError = String(e?.message ?? e)
    }
  }

  async function refreshSnapshot() {
    await sceneState.loadScene()
    // Scene loader stores errors in state.error; in real mode surface them in the HUD.
    if (isRealMode.value && state.error) {
      // Typical case: HTTP 404 when run_id was persisted but backend restarted.
      if (state.error.includes('HTTP 404')) {
        real.runId = null
        real.runStatus = null
        real.lastEventId = null
        real.artifacts = []
        stopSse()
        real.lastError = 'Run not found (server restarted). Click Start run.'
        return
      }
      real.lastError = state.error
    }
  }

  // Also surface initial scene-load errors (e.g., "No run started") that happen on mount
  // before the user clicks "Start run".
  watch(
    () => state.error,
    (e) => {
      if (!isRealMode.value) return
      if (!e) return

      if (e.includes('HTTP 404')) {
        real.runId = null
        real.runStatus = null
        real.lastEventId = null
        real.artifacts = []
        stopSse()
        real.lastError = 'Run not found (server restarted). Click Start run.'
        return
      }

      real.lastError = e
    },
    { flush: 'post' },
  )

  function nextBackoff(attempt: number): number {
    const steps = [1000, 2000, 5000, 10000, 20000]
    const base = steps[Math.min(steps.length - 1, attempt)]!
    const jitter = 0.2 * base * (Math.random() - 0.5)
    return Math.max(250, Math.round(base + jitter))
  }

  async function runSseLoop() {
    const mySeq = ++sseSeq
    stopSse()

    if (!isRealMode.value) return
    if (!real.runId || !real.accessToken) return

    const ctrl = new AbortController()
    sseAbort = ctrl

    let attempt = 0
    real.sseState = 'connecting'
    real.lastError = ''

    while (!ctrl.signal.aborted && mySeq === sseSeq) {
      const runId = real.runId
      const eqNow = effectiveEq.value
      const url = `${real.apiBase.replace(/\/+$/, '')}/simulator/runs/${encodeURIComponent(runId)}/events?equivalent=${encodeURIComponent(
        eqNow,
      )}`

      try {
        real.sseState = 'open'
        await connectSse({
          url,
          headers: authHeaders(real.accessToken),
          lastEventId: real.lastEventId,
          signal: ctrl.signal,
          onMessage: (msg) => {
            if (ctrl.signal.aborted) return
            if (msg.id) real.lastEventId = msg.id
            if (!msg.data) return

            let parsed: unknown
            try {
              parsed = JSON.parse(msg.data)
            } catch {
              return
            }

            const evt = normalizeSimulatorEvent(parsed)
            if (!evt) return

            // Prefer payload event_id when present.
            if ((evt as any).event_id) real.lastEventId = String((evt as any).event_id)

            if ((evt as any).type === 'run_status') {
              real.runStatus = evt as any
              real.intensityPercent = Math.round(Number((evt as any).intensity_percent ?? real.intensityPercent))
              const le = (evt as any).last_error
              real.lastError = le ? `${le.code}: ${le.message}` : ''
              return
            }

            if ((evt as any).type === 'tx.updated') {
              sceneAndDemo.runTxOnce // keep deps referenced (no-op)
              demoPlayer.runTxEvent(evt as any)
              return
            }

            if ((evt as any).type === 'tx.failed') {
              const code = String((evt as any).error?.code ?? 'TX_FAILED')
              const from = String((evt as any).from ?? '')
              const to = String((evt as any).to ?? '')
              if (from && to) addActiveEdge(keyEdge(from, to), 1400)
              if (to) {
                pushFloatingLabel({
                  nodeId: to,
                  text: `FAIL ${code}`,
                  color: '#f87171',
                  ttlMs: 2400,
                  offsetYPx: -6,
                  throttleKey: `tx_failed:${to}`,
                  throttleMs: 120,
                })
              }
              return
            }

            if ((evt as any).type === 'clearing.plan') {
              const planId = String((evt as any).plan_id ?? '')
              if (planId) clearingPlansById.set(planId, evt as any)
              return
            }

            if ((evt as any).type === 'clearing.done') {
              const planId = String((evt as any).plan_id ?? '')
              const plan = planId ? clearingPlansById.get(planId) : undefined
              if (plan) {
                clearingPlansById.delete(planId)
                demoPlayer.runClearingOnce(plan, evt as any)
              } else {
                // Fallback: apply patches immediately, without animation.
                realPatchApplier.applyNodePatches((evt as any).node_patch)
                realPatchApplier.applyEdgePatches((evt as any).edge_patch)
                resetOverlays()
              }
            }
          },
        })

        // Stream ended (server may close on stopped/error). Refresh status for UI.
        await refreshRunStatus()

        const st = String(real.runStatus?.state ?? '')
        if (st === 'stopped' || st === 'error') {
          real.sseState = 'closed'
          return
        }

        real.sseState = 'reconnecting'
      } catch (e: any) {
        if (ctrl.signal.aborted) return

        const msg = String(e?.message ?? e)
        real.lastError = msg

        // Strict replay mode: backend may return 410 → do a full refresh.
        if (msg.includes(' 410 ') || msg.includes('HTTP 410') || msg.includes('status 410')) {
          real.lastEventId = null
          await refreshRunStatus()
          await refreshSnapshot()
        }

        real.sseState = 'reconnecting'
      }

      const delay = nextBackoff(attempt++)
      await new Promise((r) => setTimeout(r, delay))
    }
  }

  watch(
    () => [apiMode.value, real.runId, real.accessToken, effectiveEq.value, real.apiBase] as const,
    () => {
      if (!isRealMode.value) {
        stopSse()
        return
      }
      // Reconnect when run/token/eq/base changes.
      runSseLoop()
    },
    { flush: 'post' },
  )

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

  async function refreshScenarios() {
    if (!real.accessToken) {
      real.lastError = 'Missing access token'
      return
    }

    real.loadingScenarios = true
    real.lastError = ''
    try {
      const res = await listScenarios({ apiBase: real.apiBase, accessToken: real.accessToken })
      real.scenarios = res.items ?? []
      if (!real.selectedScenarioId && real.scenarios.length) {
        real.selectedScenarioId = real.scenarios[0]!.scenario_id
      }
    } catch (e: any) {
      real.lastError = String(e?.message ?? e)
    } finally {
      real.loadingScenarios = false
    }
  }

  async function startRun() {
    if (!real.selectedScenarioId) return
    if (!real.accessToken) {
      real.lastError = 'Missing access token'
      return
    }

    real.lastError = ''
    try {
      const res = await createRun(
        { apiBase: real.apiBase, accessToken: real.accessToken },
        { scenario_id: real.selectedScenarioId, mode: real.desiredMode, intensity_percent: real.intensityPercent },
      )
      real.runId = res.run_id
      real.runStatus = null
      real.lastEventId = null
      real.artifacts = []

      await refreshRunStatus()
      await refreshSnapshot()
      await runSseLoop()
    } catch (e: any) {
      real.lastError = String(e?.message ?? e)
    }
  }

  async function pause() {
    if (!real.runId || !real.accessToken) return
    try {
      real.runStatus = await pauseRun({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId)
    } catch (e: any) {
      real.lastError = String(e?.message ?? e)
    }
  }

  async function resume() {
    if (!real.runId || !real.accessToken) return
    try {
      real.runStatus = await resumeRun({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId)
    } catch (e: any) {
      real.lastError = String(e?.message ?? e)
    }
  }

  async function stop() {
    if (!real.runId || !real.accessToken) return
    try {
      real.runStatus = await stopRun({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId)
      // Server may close SSE; keep UI consistent.
      await refreshRunStatus()
    } catch (e: any) {
      real.lastError = String(e?.message ?? e)
    }
  }

  async function applyIntensity() {
    if (!real.runId || !real.accessToken) return
    try {
      real.runStatus = await setIntensity({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId, real.intensityPercent)
    } catch (e: any) {
      real.lastError = String(e?.message ?? e)
    }
  }

  async function refreshArtifacts() {
    if (!real.runId || !real.accessToken) return
    real.artifactsLoading = true
    try {
      const idx = await listArtifacts({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId)
      real.artifacts = idx.items ?? []
    } catch (e: any) {
      real.lastError = String(e?.message ?? e)
    } finally {
      real.artifactsLoading = false
    }
  }

  async function downloadArtifact(name: string) {
    if (!real.runId || !real.accessToken) return
    try {
      const url = artifactDownloadUrl({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId, name)
      const res = await fetch(url, { headers: authHeaders(real.accessToken) })
      if (!res.ok) throw new Error(`Download failed: ${res.status} ${res.statusText}`)
      const blob = await res.blob()
      const blobUrl = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = blobUrl
      a.download = name
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(blobUrl)
    } catch (e: any) {
      real.lastError = String(e?.message ?? e)
    }
  }

  // Preload scenarios on first real-mode mount.
  watch(
    () => isRealMode.value,
    (v) => {
      if (!v) return
      void (async () => {
        await refreshScenarios()

        // If run_id was persisted from a previous session, try to restore UI state.
        // If the backend restarted, this will clear the stale run_id and tell the user.
        if (real.runId && real.accessToken) {
          await refreshRunStatus()
          if (real.runId) {
            await refreshSnapshot()
            if (real.runId) {
              await runSseLoop()
            }
          }
          return
        }

        // Dev UX: if user opened /?mode=real on localhost, auto-start a run once scenarios loaded.
        // Avoid doing this in non-localhost environments.
        if (import.meta.env.DEV && isLocalhost && real.accessToken && real.selectedScenarioId && !real.runId) {
          await startRun()
        }
      })()
    },
    { immediate: true },
  )

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
      refreshScenarios,
      startRun,
      pause,
      resume,
      stop,
      applyIntensity,
      refreshSnapshot,
      refreshArtifacts,
      downloadArtifact,
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
