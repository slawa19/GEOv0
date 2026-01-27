import { computed, reactive, ref } from 'vue'

import { loadEvents, loadSnapshot } from '../fixtures'
import { assertPlaylistEdgesExistInSnapshot } from '../demo/playlistValidation'
import { computeLayoutForMode, type LayoutMode } from '../layout/forceLayout'
import { fillForNode, sizeForNode } from '../render/nodePainter'
import type { ClearingDoneEvent, ClearingPlanEvent, GraphSnapshot, TxUpdatedEvent } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'
import type { LabelsLod, Quality } from '../types/uiPrefs'
import type { SceneId } from '../scenes'
import { VIZ_MAPPING } from '../vizMapping'

import { useAppDemoPlayerSetup } from './useAppDemoPlayerSetup'
import { useAppLifecycle } from './useAppLifecycle'
import { useAppUiDerivedState } from './useAppUiDerivedState'
import { useAppViewAndNodeCard } from './useAppViewAndNodeCard'
import { useCanvasInteractions } from './useCanvasInteractions'
import { useLabelNodes } from './useLabelNodes'
import { useLayoutIndex } from './useLayoutIndex'
import { usePersistedSimulatorPrefs } from './usePersistedSimulatorPrefs'
import { useSelectedNodeEdgeStats } from './useSelectedNodeEdgeStats'
import { useSnapshotIndex } from './useSnapshotIndex'
import { useAppDragToPinAndPreview } from './useAppDragToPinAndPreview'
import { useNodeSelectionAndCardOpen } from './useNodeSelectionAndCardOpen'
import { useAppSceneAndDemo } from './useAppSceneAndDemo'
import { useAppPickingAndHover } from './useAppPickingAndHover'
import { useAppFxAndRender } from './useAppFxAndRender'
import { useAppPhysicsAndPinningWiring } from './useAppPhysicsAndPinningWiring'
import { useAppLayoutWiring } from './useAppLayoutWiring'

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
    demoTxEvents: [] as TxUpdatedEvent[],
    demoClearingPlan: null as ClearingPlanEvent | null,
    demoClearingDone: null as ClearingDoneEvent | null,
    selectedNodeId: null as string | null,
    flash: 0 as number,
  })

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
  const edgeTooltipStyle = pickingAndHover.edgeTooltipStyle


  const dragToPinAndPreview = useAppDragToPinAndPreview({
    dragPreviewEl,
    isTestMode: () => isTestMode.value,
    pickNodeAt,
    getLayoutNodeById: (id) => layoutIndex.value.nodeById.get(id) ?? null,
    setSelectedNodeId: selectNode,
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
    setSelectedNodeId: selectNode,
    setNodeCardOpen,
    clearHoveredEdge,
    dragToPin,
    cameraSystem,
    edgeHover,
    getPanActive: () => panState.active,
  })

  const onCanvasClick = canvasInteractions.onCanvasClick
  const onCanvasDblClick = canvasInteractions.onCanvasDblClick
  const onCanvasPointerDown = canvasInteractions.onCanvasPointerDown
  const onCanvasPointerMove = canvasInteractions.onCanvasPointerMove
  const onCanvasPointerUp = canvasInteractions.onCanvasPointerUp
  const onCanvasWheel = canvasInteractions.onCanvasWheel

  const resetView = viewControls.resetView

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
    loadSnapshot,
    loadEvents,
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

  const runTxOnce = sceneAndDemo.runTxOnce
  const runClearingOnce = sceneAndDemo.runClearingOnce
  const canDemoPlay = sceneAndDemo.canDemoPlay
  const demoPlayLabel = sceneAndDemo.demoPlayLabel
  const demoStepOnce = sceneAndDemo.demoStepOnce
  const demoTogglePlay = sceneAndDemo.demoTogglePlay
  const demoReset = sceneAndDemo.demoReset

  const sceneState = sceneAndDemo.sceneState

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
    isNodeCardOpen,
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
