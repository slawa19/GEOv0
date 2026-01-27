<script setup lang="ts">
import DemoHudBottom from './components/DemoHudBottom.vue'
import DemoHudTop from './components/DemoHudTop.vue'
import EdgeTooltip from './components/EdgeTooltip.vue'
import LabelsOverlayLayers from './components/LabelsOverlayLayers.vue'
import NodeCardOverlay from './components/NodeCardOverlay.vue'
import { computed, onMounted, onUnmounted, reactive, ref, toRaw, watch } from 'vue'
import type {
  ClearingDoneEvent,
  ClearingPlanEvent,
  DemoEvent,
  EdgePatch,
  GraphLink,
  GraphNode,
  GraphSnapshot,
  NodePatch,
  TxUpdatedEvent,
} from './types'
import { loadEvents, loadSnapshot } from './fixtures'
import { VIZ_MAPPING } from './vizMapping'
import { drawBaseGraph, type LayoutLink as RenderLayoutLink } from './render/baseGraph'
import { fillForNode, sizeForNode, type LayoutNode as RenderLayoutNode } from './render/nodePainter'
import { withAlpha } from './render/color'
import { createFxState, renderFxFrame, resetFxState, spawnEdgePulses, spawnNodeBursts, spawnSparks } from './render/fxRenderer'
import { createTimerRegistry } from './demo/timerRegistry'
import { createPatchApplier } from './demo/patches'
import { assertPlaylistEdgesExistInSnapshot } from './demo/playlistValidation'
import { installGeoSimDevHook } from './dev/geoSimDevHook'
import { useDemoPlayer } from './composables/useDemoPlayer'
import { useCamera } from './composables/useCamera'
import { useLayoutCoordinator } from './composables/useLayoutCoordinator'
import { useOverlayState } from './composables/useOverlayState'
import { usePicking } from './composables/usePicking'
import { useEdgeHover } from './composables/useEdgeHover'
import { useEdgeTooltip } from './composables/useEdgeTooltip'
import { useNodeCard } from './composables/useNodeCard'
import { useDragPreview } from './composables/useDragPreview'
import { useDragToPinInteraction } from './composables/useDragToPinInteraction'
import { useDemoActions } from './composables/useDemoActions'
import { useDemoPlaybackControls } from './composables/useDemoPlaybackControls'
import { usePersistedSimulatorPrefs } from './composables/usePersistedSimulatorPrefs'
import { createPhysicsManager } from './composables/usePhysicsManager'
import { usePinning } from './composables/usePinning'
import { useRenderLoop } from './composables/useRenderLoop'
import { useSceneState } from './composables/useSceneState'
import { computeLayoutForMode, type LayoutMode } from './layout/forceLayout'
import { fnv1a } from './utils/hash'
import { keyEdge } from './utils/edgeKey'
import { clamp, clamp01 } from './utils/math'
import { asFiniteNumber, formatAmount2 } from './utils/numberFormat'
import { isDemoScene, SCENES, SCENE_IDS, type SceneId } from './scenes'
import type { LayoutLink, LayoutNode } from './types/layout'
import type { LabelsLod, Quality } from './types/uiPrefs'

const eq = ref('UAH')
const scene = ref<SceneId>('A')

const layoutMode = ref<LayoutMode>('admin-force')

const isDemoFixtures = computed(() => String(import.meta.env.VITE_DEMO_FIXTURES ?? '1') === '1')
const isTestMode = computed(() => String(import.meta.env.VITE_TEST_MODE ?? '0') === '1')

// Playwright sets navigator.webdriver=true. Use it to keep screenshot tests stable even if
// someone runs the dev server with VITE_TEST_MODE=1.
const isWebDriver = typeof navigator !== 'undefined' && (navigator as any).webdriver === true

const effectiveEq = computed(() => {
  // Scene E must use canonical clearing cycles (UAH) per spec.
  if (scene.value === 'E') return 'UAH'
  // Keep Playwright / test-mode stable even if query params are present.
  if (isTestMode.value) return 'UAH'
  return eq.value
})

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

const canvasEl = ref<HTMLCanvasElement | null>(null)
const fxCanvasEl = ref<HTMLCanvasElement | null>(null)
const hostEl = ref<HTMLDivElement | null>(null)
const dragPreviewEl = ref<HTMLDivElement | null>(null)

function getNodeById(id: string | null): GraphNode | null {
  if (!id || !state.snapshot) return null
  return state.snapshot.nodes.find((n) => n.id === id) ?? null
}

function fxColorForNode(nodeId: string, fallback: string): string {
  const n = getNodeById(nodeId)
  if (!n) return fallback
  return fillForNode(n, VIZ_MAPPING)
}

const labelsLod = ref<LabelsLod>('selection')
const quality = ref<Quality>('high')

const dprClamp = computed(() => {
  if (isTestMode.value) return 1
  if (quality.value === 'low') return 1
  if (quality.value === 'med') return 1.5
  return 2
})

const showDemoControls = computed(() => !isTestMode.value && isDemoScene(scene.value))
const showResetView = computed(() => !isTestMode.value)

const baselineLayoutPos = new Map<string, { x: number; y: number }>()
const pinnedPos = reactive(new Map<string, { x: number; y: number }>())

const snapshotRef = computed(() => state.snapshot)

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
const requestRelayoutDebounced = layoutCoordinator.requestRelayoutDebounced

const persistedPrefs = usePersistedSimulatorPrefs({
  layoutMode,
  quality,
  labelsLod,
  requestResizeAndLayout,
})

const overlayLabelScale = computed(() => {
  // Keep e2e screenshots stable and avoid surprises in test-mode.
  if (isTestMode.value || isWebDriver) return 1
  // When zoomed out (z<1), make overlay text visually smaller so it "moves away" with nodes.
  // Clamp to avoid becoming unreadable or comically large.
  const z = Math.max(0.01, camera.zoom)
  return clamp(z, 0.65, 1)
})

const cameraSystem = useCamera({
  canvasEl,
  hostEl,
  getLayoutNodes: () => layout.nodes,
  getLayoutW: () => layout.w,
  getLayoutH: () => layout.h,
  isTestMode: () => isTestMode.value,
})

layoutCoordinator.setClampCameraPan(cameraSystem.clampCameraPan)

const camera = cameraSystem.camera
const panState = cameraSystem.panState
const wheelState = cameraSystem.wheelState

const resetCamera = cameraSystem.resetCamera
const getWorldBounds = cameraSystem.getWorldBounds
const clampCameraPan = cameraSystem.clampCameraPan
const worldToScreen = cameraSystem.worldToScreen
const screenToWorld = cameraSystem.screenToWorld
const worldToCssTranslate = cameraSystem.worldToCssTranslate
const clientToScreen = cameraSystem.clientToScreen

function worldToCssTranslateNoScale(x: number, y: number) {
  const p = worldToScreen(x, y)
  return `translate3d(${p.x}px, ${p.y}px, 0)`
}

const nodeCard = useNodeCard({
  hostEl,
  selectedNodeId: computed({
    get: () => state.selectedNodeId,
    set: (v) => {
      state.selectedNodeId = v
    },
  }),
  getNodeById,
  getLayoutNodeById: (id) => layout.nodes.find((n) => n.id === id) ?? null,
  getNodeScreenSize: (n) => sizeForNode(n),
  worldToScreen,
  // New: provide edge info for smart card positioning
  getIncidentEdges: (nodeId) => layout.links.filter((l) => l.source === nodeId || l.target === nodeId),
  getLayoutNodes: () => layout.nodes,
})

const selectedNode = nodeCard.selectedNode
const nodeCardStyle = nodeCard.nodeCardStyle

const selectedNodeEdgeStats = computed(() => {
  const snapshot = state.snapshot
  const id = state.selectedNodeId
  if (!snapshot || !id) return null

  let inLimit = 0
  let outLimit = 0
  let degree = 0

  for (const l of snapshot.links) {
    const limit = asFiniteNumber(l.trust_limit)
    if (l.source === id) {
      outLimit += limit
      degree += 1
      continue
    }
    if (l.target === id) {
      inLimit += limit
      degree += 1
    }
  }

  return {
    inLimitText: formatAmount2(inLimit),
    outLimitText: formatAmount2(outLimit),
    degree,
  }
})

const fxState = createFxState()


const timers = createTimerRegistry()

const overlayState = useOverlayState<LayoutNode>({
  getLayoutNodeById: (id) => layout.nodes.find((n) => n.id === id),
  sizeForNode,
  getCameraZoom: () => camera.zoom,
  setFlash: (v) => {
    state.flash = v
  },
  resetFxState: () => resetFxState(fxState),
})

const hoveredEdge = overlayState.hoveredEdge
const clearHoveredEdge = overlayState.clearHoveredEdge
const activeEdges = overlayState.activeEdges
const addActiveEdge = overlayState.addActiveEdge
const pruneActiveEdges = overlayState.pruneActiveEdges
const pushFloatingLabel = overlayState.pushFloatingLabel
const pruneFloatingLabels = overlayState.pruneFloatingLabels
const floatingLabelsView = overlayState.floatingLabelsView
const resetOverlays = overlayState.resetOverlays

type FloatingLabelFx = (typeof floatingLabelsView.value)[number] & { glow: number }

const floatingLabelsViewFx = computed((): FloatingLabelFx[] => {
  const base = floatingLabelsView.value
  if (base.length === 0) return []

  // Keep Playwright screenshots stable.
  if (isWebDriver) return base.map((fl) => ({ ...fl, glow: 0 }))

  // Trigger a soft glow when a label is close enough to any node
  // that it could visually merge with it.
  const nodes = layout.nodes
  const padPx = 12
  const falloffPx = 36

  return base.map((fl) => {
    const lp = worldToScreen(fl.x, fl.y)
    let best = 0

    for (const n of nodes) {
      const sz = sizeForNode(n)
      const rPx = Math.max(6, Math.max(sz.w, sz.h) / 2)
      const np = worldToScreen(n.__x, n.__y)

      const dx = lp.x - np.x
      const dy = lp.y - np.y

      // Fast reject.
      if (Math.abs(dx) > rPx + padPx + falloffPx) continue
      if (Math.abs(dy) > rPx + padPx + falloffPx) continue

      const boundary = rPx + padPx
      const d = Math.hypot(dx, dy)
      const t = clamp01(1 - (d - boundary) / falloffPx)
      if (t > best) best = t
      if (best >= 0.98) break
    }

    return { ...fl, glow: best }
  })
})

function scheduleTimeout(fn: () => void, delayMs: number) {
  return timers.schedule(fn, delayMs)
}

function clearScheduledTimeouts() {
  timers.clearAll()
}

function computeLayout(snapshot: GraphSnapshot, w: number, h: number, mode: LayoutMode) {
  const result = computeLayoutForMode(snapshot, w, h, mode, isTestMode.value)
  layout.nodes = result.nodes
  layout.links = result.links

  pinning.captureBaseline(result.nodes)
  // Re-apply pinned positions after any relayout.
  pinning.reapplyPinnedToLayout()

  // Layout arrays are replaced on every relayout, so the physics engine must be recreated.
  physics.recreateForCurrentLayout({ w, h })
}

const physics = createPhysicsManager({
  // Keep e2e + deterministic screenshots stable.
  isEnabled: () => !isTestMode.value,
  getLayoutNodes: () => layout.nodes,
  getLayoutLinks: () => layout.links,
  getQuality: () => quality.value,
  getPinnedPos: () => pinnedPos,
})

const pinning = usePinning({
  pinnedPos,
  baselineLayoutPos,
  getSelectedNodeId: () => state.selectedNodeId,
  getLayoutNodeById: (id) => layout.nodes.find((n) => n.id === id) ?? null,
  physics: {
    pin: (id, x, y) => physics.pin(id, x, y),
    unpin: (id) => physics.unpin(id),
    syncFromLayout: () => physics.syncFromLayout(),
    reheat: (alpha) => physics.reheat(alpha),
  },
})

const isSelectedPinned = pinning.isSelectedPinned
const pinSelectedNode = pinning.pinSelectedNode
const unpinSelectedNode = pinning.unpinSelectedNode

// Keep physics viewport in sync even when resize does not trigger a relayout.
watch(
  () => [layout.w, layout.h] as const,
  ([w, h], [prevW, prevH]) => {
    if (w === prevW && h === prevH) return
    physics.updateViewport(w, h, 0.15)
  },
)

const demoPlayer = useDemoPlayer({
  applyPatches: applyPatchesFromEvent,
  spawnSparks: (opts) => spawnSparks(fxState, opts),
  spawnNodeBursts: (opts) => spawnNodeBursts(fxState, opts),
  spawnEdgePulses: (opts) => spawnEdgePulses(fxState, opts),
  pushFloatingLabel,
  resetOverlays,
  fxColorForNode,
  addActiveEdge,
  scheduleTimeout,
  clearScheduledTimeouts,
  getLayoutNode: (id) => layout.nodes.find((n) => n.id === id),
  isTestMode: () => isTestMode.value,
  isWebDriver,
  effectiveEq: () => effectiveEq.value,
  keyEdge,
  seedFn: fnv1a,
  edgeDirCaption,
  txSparkCore: VIZ_MAPPING.fx.tx_spark.core,
  txSparkTrail: VIZ_MAPPING.fx.tx_spark.trail,
  clearingFlashFallback: '#fbbf24',
})

const playlist = demoPlayer.playlist

function stopPlaylistPlayback() {
  demoPlayer.stopPlaylistPlayback()
}

function resetPlaylistPointers() {
  demoPlayer.resetPlaylistPointers()
}

function resetDemoState() {
  demoPlayer.resetDemoState()
  state.selectedNodeId = null
}

const layoutIndex = computed(() => {
  const nodeById = new Map<string, LayoutNode>()
  for (const n of layout.nodes) nodeById.set(n.id, n)

  const linkByKey = new Map<string, LayoutLink>()
  const incidentEdgeKeysByNodeId = new Map<string, Set<string>>()

  for (const l of layout.links) {
    linkByKey.set(l.__key, l)

    const a = l.source
    const b = l.target
    const sA = incidentEdgeKeysByNodeId.get(a) ?? new Set<string>()
    sA.add(l.__key)
    incidentEdgeKeysByNodeId.set(a, sA)

    const sB = incidentEdgeKeysByNodeId.get(b) ?? new Set<string>()
    sB.add(l.__key)
    incidentEdgeKeysByNodeId.set(b, sB)
  }

  return { nodeById, linkByKey, incidentEdgeKeysByNodeId }
})

const labelNodes = computed(() => {
  if (isTestMode.value) return []
  if (labelsLod.value === 'off') return []
  if (!state.snapshot || !state.selectedNodeId) return []

  const ids = new Set<string>()
  ids.add(state.selectedNodeId)

  if (labelsLod.value === 'neighbors') {
    const center = state.selectedNodeId
    for (const l of layout.links) {
      if (l.source === center) ids.add(l.target)
      else if (l.target === center) ids.add(l.source)
    }
  }

  const max = 28
  const out: Array<{ id: string; x: number; y: number; text: string; color: string }> = []
  for (const id of Array.from(ids)) {
    if (out.length >= max) break
    const ln = layout.nodes.find((n) => n.id === id)
    if (!ln) continue
    const gn = getNodeById(id)

    // Place the label below the node by a constant screen-space offset.
    const z = Math.max(0.01, camera.zoom)
    const sz = sizeForNode(ln)
    const dyPx = Math.max(sz.w, sz.h) / 2 + 14
    const dyW = dyPx / z

    out.push({
      id,
      x: ln.__x,
      y: ln.__y + dyW,
      text: gn?.name ? String(gn.name) : id,
      color: fxColorForNode(id, '#e2e8f0'),
    })
  }
  return out
})

const layoutNodeMap = computed(() => {
  return layoutIndex.value.nodeById
})

const layoutLinkMap = computed(() => {
  return layoutIndex.value.linkByKey
})

const selectedIncidentEdgeKeys = computed(() => {
  const id = state.selectedNodeId
  if (!id) return new Set<string>()
  return layoutIndex.value.incidentEdgeKeysByNodeId.get(id) ?? new Set<string>()
})

function edgeDirCaption() {
  return 'from→to'
}

const edgeTooltip = useEdgeTooltip({
  hostEl,
  hoveredEdge,
  clamp,
  getUnit: () => state.snapshot?.equivalent ?? effectiveEq.value,
})

const formatEdgeAmountText = edgeTooltip.formatEdgeAmountText
const edgeTooltipStyle = edgeTooltip.edgeTooltipStyle

const edgeHover = useEdgeHover({
  hoveredEdge,
  clearHoveredEdge,
  isWebDriver: () => isWebDriver,
  getSelectedNodeId: () => state.selectedNodeId,
  hasSelectedIncidentEdges: () => selectedIncidentEdgeKeys.value.size > 0,
  pickNodeAt: (x, y) => pickNodeAt(x, y),
  pickEdgeAt: (x, y) => pickEdgeAt(x, y),
  getLinkByKey: (k) => layoutLinkMap.value.get(k),
  formatEdgeAmountText,
  clientToScreen,
  screenToWorld,
  worldToScreen,
})

const picking = usePicking({
  getLayoutNodes: () => layout.nodes,
  getLayoutLinks: () => layout.links,
  getCameraZoom: () => camera.zoom,
  sizeForNode,
  clientToScreen,
  screenToWorld,
  isReady: () => !!hostEl.value && !!canvasEl.value,
})

function pickEdgeAt(clientX: number, clientY: number) {
  return picking.pickEdgeAt(clientX, clientY)
}

const patchApplier = createPatchApplier({
  getSnapshot: () => state.snapshot,
  getLayoutNodes: () => layout.nodes,
  getLayoutLinks: () => layout.links,
  keyEdge,
})

const applyNodePatches = patchApplier.applyNodePatches
const applyEdgePatches = patchApplier.applyEdgePatches

function applyPatchesFromEvent(evt: DemoEvent) {
  if (evt.type !== 'tx.updated' && evt.type !== 'clearing.done') return
  applyNodePatches(evt.node_patch)
  applyEdgePatches(evt.edge_patch)
}

watch(layoutMode, () => {
  // Re-layout deterministically when user switches layout mode.
  resizeAndLayout()
})

onMounted(() => {
  persistedPrefs.loadFromStorage()

  installGeoSimDevHook({
    isDev: () => import.meta.env.DEV,
    isTestMode: () => isTestMode.value,
    isWebDriver: () => isWebDriver,
    getState: () => state,
    fxState,
    runTxOnce,
    runClearingOnce,
  })
})

const renderLoop = useRenderLoop({
  canvasEl,
  fxCanvasEl,
  getSnapshot: () => state.snapshot,
  getLayout: () => ({
    w: layout.w,
    h: layout.h,
    nodes: layout.nodes as unknown as RenderLayoutNode[],
    links: layout.links as unknown as RenderLayoutLink[],
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
  drawBaseGraph,
  renderFxFrame,
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

let showDragPreviewForNode: (nodeId: string) => void = () => {}
let scheduleDragPreview: () => void = () => {}
let hideDragPreview: () => void = () => {}

const dragToPin = useDragToPinInteraction({
  isEnabled: () => !isTestMode.value,
  pickNodeAt: (x, y) => pickNodeAt(x, y),
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
  showDragPreviewForNode: (id) => showDragPreviewForNode(id),
  scheduleDragPreview: () => scheduleDragPreview(),
  hideDragPreview: () => hideDragPreview(),
})

const dragPreview = useDragPreview({
  el: dragPreviewEl,
  getDraggedNode: () => dragToPin.dragState.cachedNode,
  getNodeById: (id) => getNodeById(id),
  getCamera: () => camera,
  renderOnce,
  sizeForNode: (n) => sizeForNode(n),
  fillForNode: (n) => fillForNode(n, VIZ_MAPPING),
})

hideDragPreview = dragPreview.hideDragPreview
showDragPreviewForNode = dragPreview.showDragPreviewForNode
scheduleDragPreview = dragPreview.scheduleDragPreview

function pickNodeAt(clientX: number, clientY: number) {
  return picking.pickNodeAt(clientX, clientY)
}

function onCanvasClick(ev: MouseEvent) {
  const hit = pickNodeAt(ev.clientX, ev.clientY)
  if (!hit) {
    state.selectedNodeId = null
    return
  }
  state.selectedNodeId = hit.id
}

function onCanvasPointerDown(ev: PointerEvent) {
  // Keep tests deterministic.
  if (isTestMode.value) return

  // If user grabs a node: select it, don't pan.
  // Also allow optional drag-to-pin interaction.
  if (dragToPin.onPointerDown(ev)) return

  cameraSystem.onPointerDown(ev)
}

function onCanvasPointerMove(ev: PointerEvent) {
  if (dragToPin.onPointerMove(ev)) return

  edgeHover.onPointerMove(ev, { panActive: panState.active })

  if (!panState.active) return

  cameraSystem.onPointerMove(ev)
}

function onCanvasPointerUp(ev: PointerEvent) {
  if (dragToPin.onPointerUp(ev)) return

  const wasClick = cameraSystem.onPointerUp(ev)
  if (!wasClick) return

  // Click on empty background: clear selection.
  state.selectedNodeId = null
  clearHoveredEdge()
}


function onCanvasWheel(ev: WheelEvent) {
  // While dragging a node we rely on a camera snapshot for DOM preview; keep camera stable.
  if (dragToPin.dragState.active) return
  cameraSystem.onWheel(ev)
}

function resetView() {
  resetCamera()
  clampCameraPan()
}

const demoActions = useDemoActions({
  getSnapshot: () => state.snapshot,
  getEffectiveEq: () => effectiveEq.value,
  getDemoTxEvents: () => state.demoTxEvents,
  getDemoClearingPlan: () => state.demoClearingPlan,
  getDemoClearingDone: () => state.demoClearingDone,
  setError: (msg) => {
    state.error = msg
  },
  stopPlaylistPlayback,
  ensureRenderLoop,
  clearScheduledTimeouts,
  resetOverlays,
  loadEvents,
  assertPlaylistEdgesExistInSnapshot,
  runTxEvent: (evt) => {
    demoPlayer.runTxEvent(evt)
  },
  runClearingOnce: (plan, done) => {
    demoPlayer.runClearingOnce(plan, done)
  },
  dev: {
    isDev: () => import.meta.env.DEV,
    onTxCall: () => {
      ;(window as any).__geoSimTxCalls = ((window as any).__geoSimTxCalls ?? 0) + 1
    },
    onTxError: (msg, e) => {
      ;(window as any).__geoSimLastTxError = msg
      // eslint-disable-next-line no-console
      console.error(e)
    },
    onClearingError: (msg, e) => {
      ;(window as any).__geoSimLastClearingError = msg
      // eslint-disable-next-line no-console
      console.error(e)
    },
  },
})

const runTxOnce = demoActions.runTxOnce
const runClearingOnce = demoActions.runClearingOnce

const demoPlayback = useDemoPlaybackControls({
  getSnapshotReady: () => !!state.snapshot,
  getScene: () => scene.value,
  getDemoTxEvents: () => state.demoTxEvents,
  getDemoClearingPlan: () => state.demoClearingPlan,
  getDemoClearingDone: () => state.demoClearingDone,
  getPlaylistPlaying: () => playlist.playing,
  demoPlayer: {
    runClearingStep: (stepIndex, plan, done, opts) => demoPlayer.runClearingStep(stepIndex, plan, done, opts),
    demoStepOnce: (s, tx, plan, done) => demoPlayer.demoStepOnce(s, tx, plan, done),
    demoTogglePlay: (s, tx, plan, done) => demoPlayer.demoTogglePlay(s, tx, plan, done),
  },
  resetDemoState,
})

const canDemoPlay = demoPlayback.canDemoPlay
const demoPlayLabel = demoPlayback.demoPlayLabel
const runClearingStep = demoPlayback.runClearingStep
const demoStepOnce = demoPlayback.demoStepOnce
const demoTogglePlay = demoPlayback.demoTogglePlay
const demoReset = demoPlayback.demoReset

const sceneState = useSceneState({
  eq,
  scene,
  layoutMode,
  allowEqDeepLink: () => !isTestMode.value,
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

onMounted(() => {
  sceneState.setup()
})

onUnmounted(() => {
  hideDragPreview()
  persistedPrefs.dispose()
  physics.stop()
  sceneState.teardown()
})
</script>

<template>
  <div
    ref="hostEl"
    class="root"
    :data-ready="!state.loading && !state.error && state.snapshot ? '1' : '0'"
    :data-scene="scene"
    :data-layout="layoutMode"
    :data-webdriver="isWebDriver ? '1' : '0'"
    :style="{ '--overlay-scale': String(overlayLabelScale) }"
  >
    <canvas
      ref="canvasEl"
      class="canvas"
      @click="onCanvasClick"
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
    />

    <DemoHudTop
      v-model:eq="eq"
      v-model:layoutMode="layoutMode"
      v-model:scene="scene"
      :is-demo-fixtures="isDemoFixtures"
      :is-test-mode="isTestMode"
      :is-web-driver="isWebDriver"
      :nodes-count="state.snapshot?.nodes.length"
      :links-count="state.snapshot?.links.length"
    />

    <NodeCardOverlay
      v-if="selectedNode && !dragToPin.dragState.active"
      :node="selectedNode"
      :style="nodeCardStyle()"
      :edge-stats="selectedNodeEdgeStats"
      :equivalent-text="state.snapshot?.equivalent ?? ''"
      :show-pin-actions="!isTestMode && !isWebDriver"
      :is-pinned="isSelectedPinned"
      :pin="pinSelectedNode"
      :unpin="unpinSelectedNode"
    />

    <DemoHudBottom
      v-model:quality="quality"
      v-model:labelsLod="labelsLod"
      :show-reset-view="showResetView"
      :show-demo-controls="showDemoControls"
      :playlist-playing="playlist.playing"
      :can-demo-play="canDemoPlay"
      :demo-play-label="demoPlayLabel"
      :run-tx-once="runTxOnce"
      :run-clearing-once="runClearingOnce"
      :reset-view="resetView"
      :demo-step-once="demoStepOnce"
      :demo-toggle-play="demoTogglePlay"
      :demo-reset="demoReset"
    />

    <!-- Loading / error overlay (fail-fast, but non-intrusive) -->
    <div v-if="state.loading" class="overlay">Loading fixtures…</div>
    <div v-else-if="state.error" class="overlay overlay-error">
      <div class="overlay-title">Fixtures error</div>
      <div class="overlay-text mono">{{ state.error }}</div>
    </div>

    <LabelsOverlayLayers
      :label-nodes="labelNodes"
      :floating-labels="floatingLabelsViewFx"
      :world-to-css-translate-no-scale="worldToCssTranslateNoScale"
    />
  </div>
</template>

<style>
.root {
  position: relative;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  background: #020617;
  color: rgba(226, 232, 240, 0.9);
  font-family:
    ui-sans-serif,
    system-ui,
    -apple-system,
    Segoe UI,
    Roboto,
    Ubuntu,
    Cantarell,
    Noto Sans,
    Arial,
    "Apple Color Emoji",
    "Segoe UI Emoji";
}

.canvas {
  position: absolute;
  inset: 0;
  z-index: 1;
}

.canvas-fx {
  pointer-events: none;
  z-index: 3;
}

.root[data-webdriver='1'] .canvas-fx {
  /* Keep Playwright screenshots stable (no animated FX layer in captures). */
  z-index: 0;
}

.drag-preview {
  position: absolute;
  left: 0;
  top: 0;
  z-index: 6;
  display: none;
  pointer-events: none;
  will-change: transform;
  transform: translate3d(0, 0, 0);
  border: 1px solid rgba(255, 255, 255, 0.25);
  backdrop-filter: blur(6px);
}

.hud-top {
  position: absolute;
  top: 12px;
  left: 12px;
  right: 12px;
  z-index: 40;
  pointer-events: none;
}

.root[data-webdriver='1'] .hud-top {
  /* Avoid screenshot diffs (HUD is not part of the scene gate). */
  display: none;
}

.hud-row {
  display: flex;
  gap: 10px;
  align-items: center;
  pointer-events: auto;
}

.pill {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  padding: 8px 10px;
  border-radius: 12px;
  background: rgba(15, 23, 42, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.16);
  backdrop-filter: blur(10px);
}

.pill.subtle {
  opacity: 0.85;
}

.label {
  font-size: 12px;
  color: rgba(226, 232, 240, 0.65);
}

.select {
  background: transparent;
  color: rgba(226, 232, 240, 0.95);
  border: none;
  z-index: 3;
  font-size: 12px;
}

.value {
  z-index: 2;
  font-size: 12px;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New";
}

.node-card {
  position: absolute;
  width: 260px;
  z-index: 10;
  padding: 12px;
  border-radius: 14px;
  background: rgba(15, 23, 42, 0.78);
  border: 1px solid rgba(148, 163, 184, 0.18);
  backdrop-filter: blur(12px);
  box-shadow: 0 18px 60px rgba(0, 0, 0, 0.35);
}

.node-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}

.edge-tooltip {
  position: absolute;
  left: 0;
  top: 0;
  z-index: 55;
  pointer-events: none;
  padding: 8px 10px;
  border-radius: 12px;
  background: rgba(15, 23, 42, 0.82);
  border: 1px solid rgba(148, 163, 184, 0.18);
  backdrop-filter: blur(12px);
  box-shadow: 0 18px 60px rgba(0, 0, 0, 0.35);
  transform: translate3d(0, 0, 0);
}

.edge-tooltip-title {
  font-size: 12px;
  font-weight: 650;
  color: rgba(226, 232, 240, 0.92);
  white-space: nowrap;
  margin-bottom: 2px;
}

.edge-tooltip-amount {
  font-size: 12px;
  color: rgba(226, 232, 240, 0.78);
  white-space: nowrap;
}

.node-title {
  font-size: 13px;
  font-weight: 650;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-grid {
  font-size: 12px;
  color: rgba(226, 232, 240, 0.78);
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 12px;
}

.node-item {
  display: flex;
  gap: 6px;
  align-items: baseline;
  min-width: 0;
}

.node-actions {
  display: flex;
  gap: 8px;
}

.k {
  color: rgba(226, 232, 240, 0.55);
  margin-right: 6px;
}

.hud-bottom {
  position: absolute;
  left: 50%;
  bottom: 16px;
  transform: translateX(-50%);
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 16px;
  background: rgba(15, 23, 42, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.16);
  backdrop-filter: blur(10px);
  z-index: 30;
  pointer-events: auto;
}

.hud-divider {
  width: 1px;
  align-self: stretch;
  background: rgba(148, 163, 184, 0.16);
  margin: 2px 2px;
}

.btn {
  appearance: none;
  border: 1px solid rgba(148, 163, 184, 0.22);
  background: rgba(2, 6, 23, 0.15);
  color: rgba(226, 232, 240, 0.9);
  border-radius: 12px;
  padding: 10px 14px;
  font-size: 12px;
  cursor: pointer;
}

.btn.btn-xs {
  padding: 7px 10px;
  border-radius: 10px;
}

.btn.btn-xxs {
  padding: 5px 8px;
  border-radius: 10px;
  font-size: 11px;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn.btn-ghost {
  background: rgba(15, 23, 42, 0.3);
}

.select.select-compact {
  font-size: 12px;
}

.btn:hover {
  border-color: rgba(34, 211, 238, 0.45);
}

.overlay {
  position: absolute;
  inset: 12px;
  display: flex;
  align-items: flex-start;
  justify-content: flex-start;
  pointer-events: none;
  z-index: 20;
}

.overlay-title {
  font-size: 12px;
  font-weight: 650;
  margin-bottom: 6px;
}

.overlay-text {
  font-size: 12px;
  color: rgba(226, 232, 240, 0.78);
  max-width: 820px;
  white-space: pre-wrap;
}

.overlay,
.overlay-error {
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(15, 23, 42, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.16);
  backdrop-filter: blur(10px);
}

.overlay-error {
  border-color: rgba(248, 113, 113, 0.35);
}

.floating-layer {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 50;
  /* Isolate layout/paint of transient labels from the rest of the UI. */
  contain: layout paint;
}

.labels-layer {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 45;
  contain: layout paint;
}

.node-label {
  position: absolute;
  left: 0;
  top: 0;
  will-change: transform;
}

.node-label-inner {
  /* Centered label, placed below the node via computed world offset (no frame). */
  transform: translate3d(-50%, -50%, 0) scale(var(--overlay-scale, 1));
  font-size: 12px;
  font-weight: 650;
  padding: 1px 6px;
  border: none;
  background: transparent;
  text-align: center;
  text-shadow:
    0 0 14px rgba(0, 0, 0, 0.92),
    0 0 10px rgba(148, 163, 184, 0.42);
  filter: drop-shadow(0 8px 16px rgba(0, 0, 0, 0.18));
  white-space: nowrap;
}

.floating-label {
  position: absolute;
  left: 0;
  top: 0;
  pointer-events: none;
}

.floating-label-inner {
  display: inline-block;
  font-size: 14px;
  font-weight: 700;
  text-shadow: 0 0 10px currentColor; /* Default glow */
  animation: floatUpFade 2.6s cubic-bezier(0.16, 1, 0.3, 1) forwards;
  /* Keep centering constant; animate only a pixel offset for smooth motion. */
  transform: translate3d(-50%, -50%, 0) scale(var(--overlay-scale, 1)) translate3d(0, 0, 0);
  will-change: transform, opacity;
  backface-visibility: hidden;
  white-space: pre-line;
  text-align: center;
  line-height: 1.05;
  font-variant-numeric: tabular-nums;
}

/* Improve contrast over nodes/background, but keep WebDriver screenshots stable. */
.root:not([data-webdriver='1']) .floating-label-inner {
  /* Airy by default: subtle dark halo + neon glow. */
  text-shadow:
    0 0 12px rgba(0, 0, 0, 0.85),
    0 0 12px currentColor;
  filter: drop-shadow(0 10px 22px rgba(0, 0, 0, 0.2));
  transition: filter 120ms linear;
}

/* Only boost readability when the label is likely to merge with a node behind it. */
.root:not([data-webdriver='1']) .floating-label-inner.is-glow {
  filter:
    drop-shadow(0 0 calc(6px + 14px * var(--glow)) rgba(255, 255, 255, 0.38))
    drop-shadow(0 0 calc(8px + 18px * var(--glow)) currentColor)
    drop-shadow(0 10px 22px rgba(0, 0, 0, 0.2));
  text-shadow:
    0 0 12px rgba(0, 0, 0, 0.92),
    0 0 12px currentColor,
    0 0 calc(4px + 12px * var(--glow)) rgba(255, 255, 255, 0.52);
}

@keyframes floatUpFade {
  0% {
    opacity: 0;
    transform: translate3d(-50%, -50%, 0) scale(var(--overlay-scale, 1)) translate3d(0, 0, 0);
  }
  8% {
    opacity: 1;
    transform: translate3d(-50%, -50%, 0) scale(var(--overlay-scale, 1)) translate3d(0, -2px, 0);
  }
  60% {
    opacity: 1;
    transform: translate3d(-50%, -50%, 0) scale(var(--overlay-scale, 1)) translate3d(0, -14px, 0);
  }
  100% {
    opacity: 0;
    transform: translate3d(-50%, -50%, 0) scale(var(--overlay-scale, 1)) translate3d(0, -34px, 0);
  }
}
</style>
