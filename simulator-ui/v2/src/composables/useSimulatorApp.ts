import { computed, reactive, ref } from 'vue'

import { loadEvents, loadSnapshot } from '../fixtures'
import { assertPlaylistEdgesExistInSnapshot } from '../demo/playlistValidation'
import { computeLayoutForMode, type LayoutMode } from '../layout/forceLayout'
import { fillForNode, sizeForNode } from '../render/nodePainter'
import type { GraphSnapshot } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'
import type { LabelsLod, Quality } from '../types/uiPrefs'
import type { SceneId } from '../scenes'
import { VIZ_MAPPING } from '../vizMapping'
import { clamp } from '../utils/math'

import { createAppComputeLayout } from './useAppComputeLayout'
import { useAppDemoControls } from './useAppDemoControls'
import { useAppDemoPlayerSetup } from './useAppDemoPlayerSetup'
import { useAppFxOverlays } from './useAppFxOverlays'
import { useAppLifecycle } from './useAppLifecycle'
import { useAppPhysicsAndPinning } from './useAppPhysicsAndPinning'
import { useAppRenderLoop } from './useAppRenderLoop'
import { useAppSceneState } from './useAppSceneState'
import { useAppUiDerivedState } from './useAppUiDerivedState'
import { useAppViewAndNodeCard } from './useAppViewAndNodeCard'
import { useCanvasInteractions } from './useCanvasInteractions'
import { useEdgeHover } from './useEdgeHover'
import { useEdgeTooltip } from './useEdgeTooltip'
import { useGeoSimDevHookSetup } from './useGeoSimDevHookSetup'
import { useLabelNodes } from './useLabelNodes'
import { useLayoutCoordinator } from './useLayoutCoordinator'
import { useLayoutIndex } from './useLayoutIndex'
import { usePersistedSimulatorPrefs } from './usePersistedSimulatorPrefs'
import { usePicking } from './usePicking'
import { useSelectedNodeEdgeStats } from './useSelectedNodeEdgeStats'
import { useSnapshotIndex } from './useSnapshotIndex'
import { useAppDragToPinAndPreview } from './useAppDragToPinAndPreview'

export function useSimulatorApp() {
  const eq = ref('UAH')
  const scene = ref<SceneId>('A')

  const layoutMode = ref<LayoutMode>('admin-force')

  const isDemoFixtures = computed(() => String(import.meta.env.VITE_DEMO_FIXTURES ?? '1') === '1')
  const isTestMode = computed(() => String(import.meta.env.VITE_TEST_MODE ?? '0') === '1')

  // Playwright sets navigator.webdriver=true. Use it to keep screenshot tests stable even if
  // someone runs the dev server with VITE_TEST_MODE=1.
  const isWebDriver = typeof navigator !== 'undefined' && (navigator as any).webdriver === true

  const ALLOWED_EQS = new Set(['UAH', 'HOUR', 'EUR'])

  const state = reactive({
    loading: true,
    error: '' as string,
    sourcePath: '' as string,
    eventsPath: '' as string,
    snapshot: null as GraphSnapshot | null,
    demoTxEvents: [] as any[],
    demoClearingPlan: null as any,
    demoClearingDone: null as any,
    selectedNodeId: null as string | null,
    flash: 0 as number,
  })

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

  // uiDerived reads camera.zoom, so create it after camera exists.
  // We wire it with a getter once camera is available.
  const uiDerived = useAppUiDerivedState({
    eq,
    scene,
    quality,
    isTestMode,
    isWebDriver,
    getCameraZoom: () => camera.zoom,
  })

  const effectiveEq = uiDerived.effectiveEq
  const dprClamp = uiDerived.dprClamp
  const showDemoControls = uiDerived.showDemoControls
  const showResetView = uiDerived.showResetView
  const overlayLabelScale = uiDerived.overlayLabelScale

  const baselineLayoutPos = new Map<string, { x: number; y: number }>()
  const pinnedPos = reactive(new Map<string, { x: number; y: number }>())

  const snapshotRef = computed(() => state.snapshot)

  let computeLayoutImpl: ((snapshot: GraphSnapshot, w: number, h: number, mode: LayoutMode) => void) | null = null

  function computeLayout(snapshot: GraphSnapshot, w: number, h: number, mode: LayoutMode) {
    if (!computeLayoutImpl) throw new Error('computeLayout called before computeLayoutImpl init')
    computeLayoutImpl(snapshot, w, h, mode)
  }

  const layoutCoordinator = useLayoutCoordinator<LayoutNode, LayoutLink, LayoutMode, GraphSnapshot>({
    canvasEl,
    fxCanvasEl,
    hostEl,
    snapshot: snapshotRef,
    layoutMode,
    dprClamp,
    isTestMode,
    getSourcePath: () => state.sourcePath,
    computeLayout,
  })

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

  const viewAndNodeCard = useAppViewAndNodeCard({
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

  const cameraSystem = viewAndNodeCard.cameraSystem
  const viewControls = viewAndNodeCard.viewControls
  const nodeCard = viewAndNodeCard.nodeCard

  const camera = cameraSystem.camera
  const panState = cameraSystem.panState

  const resetCamera = cameraSystem.resetCamera
  const worldToScreen = cameraSystem.worldToScreen
  const screenToWorld = cameraSystem.screenToWorld
  const clientToScreen = cameraSystem.clientToScreen

  const worldToCssTranslateNoScale = viewControls.worldToCssTranslateNoScale

  const selectedNode = nodeCard.selectedNode
  const nodeCardStyle = nodeCard.nodeCardStyle

  const selectedNodeEdgeStats = useSelectedNodeEdgeStats({
    getSnapshot: () => state.snapshot,
    getSelectedNodeId: () => state.selectedNodeId,
  }).selectedNodeEdgeStats

  const fxOverlays = useAppFxOverlays<LayoutNode>({
    getLayoutNodeById: (id) => getLayoutNodeById(id) ?? undefined,
    sizeForNode,
    getCameraZoom: () => camera.zoom,
    setFlash: (v) => {
      state.flash = v
    },
    isWebDriver: () => isWebDriver,
    getLayoutNodes: () => layout.nodes,
    worldToScreen,
  })

  const fxState = fxOverlays.fxState
  const hoveredEdge = fxOverlays.hoveredEdge
  const clearHoveredEdge = fxOverlays.clearHoveredEdge
  const activeEdges = fxOverlays.activeEdges
  const addActiveEdge = fxOverlays.addActiveEdge
  const pruneActiveEdges = fxOverlays.pruneActiveEdges
  const pushFloatingLabel = fxOverlays.pushFloatingLabel
  const pruneFloatingLabels = fxOverlays.pruneFloatingLabels
  const resetOverlays = fxOverlays.resetOverlays
  const floatingLabelsViewFx = fxOverlays.floatingLabelsViewFx
  const scheduleTimeout = fxOverlays.scheduleTimeout
  const clearScheduledTimeouts = fxOverlays.clearScheduledTimeouts

  const physicsAndPinning = useAppPhysicsAndPinning({
    isEnabled: () => !isTestMode.value,
    getLayoutNodes: () => layout.nodes,
    getLayoutLinks: () => layout.links,
    getQuality: () => quality.value,
    getPinnedPos: () => pinnedPos,
    pinnedPos,
    baselineLayoutPos,
    getSelectedNodeId: () => state.selectedNodeId,
    getLayoutNodeById: (id) => getLayoutNodeById(id),
  })

  const physics = physicsAndPinning.physics
  const pinning = physicsAndPinning.pinning
  const isSelectedPinned = physicsAndPinning.isSelectedPinned
  const pinSelectedNode = physicsAndPinning.pinSelectedNode
  const unpinSelectedNode = physicsAndPinning.unpinSelectedNode

  computeLayoutImpl = createAppComputeLayout<GraphSnapshot, LayoutMode, LayoutNode, LayoutLink>({
    isTestMode: () => isTestMode.value,
    computeLayoutForMode,
    setLayout: (nodes, links) => {
      layout.nodes = nodes
      layout.links = links
    },
    onAfterLayout: (result, ctx) => {
      pinning.captureBaseline(result.nodes)
      pinning.reapplyPinnedToLayout()
      physics.recreateForCurrentLayout({ w: ctx.w, h: ctx.h })
    },
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

  const edgeTooltip = useEdgeTooltip({
    hostEl,
    hoveredEdge,
    clamp,
    getUnit: () => state.snapshot?.equivalent ?? effectiveEq.value,
  })

  const formatEdgeAmountText = edgeTooltip.formatEdgeAmountText
  const edgeTooltipStyle = edgeTooltip.edgeTooltipStyle

  const picking = usePicking({
    getLayoutNodes: () => layout.nodes,
    getLayoutLinks: () => layout.links,
    getCameraZoom: () => camera.zoom,
    sizeForNode,
    clientToScreen,
    screenToWorld,
    isReady: () => !!hostEl.value && !!canvasEl.value,
  })

  const pickNodeAt = picking.pickNodeAt
  const pickEdgeAt = picking.pickEdgeAt

  const edgeHover = useEdgeHover({
    hoveredEdge,
    clearHoveredEdge,
    isWebDriver: () => isWebDriver,
    getSelectedNodeId: () => state.selectedNodeId,
    hasSelectedIncidentEdges: () => selectedIncidentEdgeKeys.value.size > 0,
    pickNodeAt,
    pickEdgeAt,
    getLinkByKey: (k) => layoutLinkMap.value.get(k),
    formatEdgeAmountText,
    clientToScreen,
    screenToWorld,
    worldToScreen,
  })

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
    getFlash: () => state.flash,
    setFlash: (v) => {
      state.flash = v
    },
    pruneActiveEdges,
    pruneFloatingLabels,
    mapping: VIZ_MAPPING,
    fxState,
    getSelectedNodeId: () => state.selectedNodeId,
    activeEdges,
    getLinkLod: () => (dragToPin.dragState.active && dragToPin.dragState.dragging ? 'focus' : 'full'),
    getHiddenNodeId: () => (dragToPin.dragState.active && dragToPin.dragState.dragging ? dragToPin.dragState.nodeId : null),
    beforeDraw: () => {
      physics.tickAndSyncToLayout()
    },
  })

  const ensureRenderLoop = renderLoop.ensureRenderLoop
  const stopRenderLoop = renderLoop.stopRenderLoop
  const renderOnce = renderLoop.renderOnce

  const dragToPinAndPreview = useAppDragToPinAndPreview({
    dragPreviewEl,
    isTestMode: () => isTestMode.value,
    pickNodeAt,
    getLayoutNodeById: (id) => layoutIndex.value.nodeById.get(id) ?? null,
    setSelectedNodeId: (id) => {
      state.selectedNodeId = id
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
    sizeForNode: (n) => sizeForNode(n),
    fillForNode: (n) => fillForNode(n, VIZ_MAPPING),
  })

  const dragToPin = dragToPinAndPreview.dragToPin
  const hideDragPreview = dragToPinAndPreview.hideDragPreview

  const canvasInteractions = useCanvasInteractions({
    isTestMode: () => isTestMode.value,
    pickNodeAt,
    setSelectedNodeId: (id) => {
      state.selectedNodeId = id
    },
    clearHoveredEdge,
    dragToPin,
    cameraSystem,
    edgeHover,
    getPanActive: () => panState.active,
  })

  const onCanvasClick = canvasInteractions.onCanvasClick
  const onCanvasPointerDown = canvasInteractions.onCanvasPointerDown
  const onCanvasPointerMove = canvasInteractions.onCanvasPointerMove
  const onCanvasPointerUp = canvasInteractions.onCanvasPointerUp
  const onCanvasWheel = canvasInteractions.onCanvasWheel

  const resetView = viewControls.resetView

  const demoControls = useAppDemoControls({
    scene,
    isDev: () => import.meta.env.DEV,
    getSnapshot: () => state.snapshot,
    getEffectiveEq: () => effectiveEq.value,
    getDemoTxEvents: () => state.demoTxEvents,
    getDemoClearingPlan: () => state.demoClearingPlan,
    getDemoClearingDone: () => state.demoClearingDone,
    setError: (msg) => {
      state.error = msg
    },
    setSelectedNodeId: (id) => {
      state.selectedNodeId = id
    },
    demoPlayer,
    ensureRenderLoop,
    clearScheduledTimeouts,
    resetOverlays,
    loadEvents,
    assertPlaylistEdgesExistInSnapshot,
  })

  const runTxOnce = demoControls.runTxOnce
  const runClearingOnce = demoControls.runClearingOnce
  const canDemoPlay = demoControls.canDemoPlay
  const demoPlayLabel = demoControls.demoPlayLabel
  const demoStepOnce = demoControls.demoStepOnce
  const demoTogglePlay = demoControls.demoTogglePlay
  const demoReset = demoControls.demoReset
  const resetPlaylistPointers = demoControls.resetPlaylistPointers

  const sceneState = useAppSceneState({
    eq,
    scene,
    layoutMode,
    isTestMode: () => isTestMode.value,
    isEqAllowed: (v) => ALLOWED_EQS.has(String(v ?? '').toUpperCase()),
    effectiveEq,
    state,
    loadSnapshot,
    loadEvents,
    assertPlaylistEdgesExistInSnapshot,
    clearScheduledTimeouts,
    resetPlaylistPointers,
    resetCamera,
    resetLayoutKeyCache: () => layoutCoordinator.resetLayoutKeyCache(),
    resetOverlays,
    resizeAndLayout,
    ensureRenderLoop,
    setupResizeListener: () => layoutCoordinator.setupResizeListener(),
    teardownResizeListener: () => layoutCoordinator.teardownResizeListener(),
    stopRenderLoop,
  })

  const devHook = useGeoSimDevHookSetup({
    isDev: () => import.meta.env.DEV,
    isTestMode: () => isTestMode.value,
    isWebDriver: () => isWebDriver,
    getState: () => state,
    fxState,
    runTxOnce,
    runClearingOnce,
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
    // flags
    isDemoFixtures,
    isTestMode,
    isWebDriver,

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
    onCanvasPointerDown,
    onCanvasPointerMove,
    onCanvasPointerUp,
    onCanvasWheel,

    // demo
    playlist,
    canDemoPlay,
    demoPlayLabel,
    runTxOnce,
    runClearingOnce,
    demoStepOnce,
    demoTogglePlay,
    demoReset,

    // labels
    labelNodes,
    floatingLabelsViewFx,
    worldToCssTranslateNoScale,

    // helpers for template
    getNodeById,
    resetView,
  }
}
