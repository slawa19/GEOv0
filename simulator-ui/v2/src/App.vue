<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
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
import { createFxState, renderFxFrame, resetFxState, spawnEdgePulses, spawnNodeBursts, spawnSparks } from './render/fxRenderer'
import { createTimerRegistry } from './demo/timerRegistry'
import { useDemoPlayer } from './composables/useDemoPlayer'
import { useCamera } from './composables/useCamera'
import { useLayoutCoordinator } from './composables/useLayoutCoordinator'
import { useOverlayState } from './composables/useOverlayState'
import { closestPointOnSegment, usePicking } from './composables/usePicking'
import { useEdgeTooltip } from './composables/useEdgeTooltip'
import { useNodeCard } from './composables/useNodeCard'
import { useRenderLoop } from './composables/useRenderLoop'
import { useSceneState } from './composables/useSceneState'
import { computeLayoutForMode, type LayoutMode } from './layout/forceLayout'
import { fnv1a } from './utils/hash'

type SceneId = 'A' | 'B' | 'C' | 'D' | 'E'

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

function keyEdge(a: string, b: string) {
  return `${a}→${b}`
}

function getNodeById(id: string | null): GraphNode | null {
  if (!id || !state.snapshot) return null
  return state.snapshot.nodes.find((n) => n.id === id) ?? null
}

function fxColorForNode(nodeId: string, fallback: string): string {
  const n = getNodeById(nodeId)
  if (!n) return fallback
  return fillForNode(n, VIZ_MAPPING)
}

function asFiniteNumber(v: unknown): number {
  if (typeof v === 'number') return Number.isFinite(v) ? v : 0
  if (typeof v === 'string') {
    const n = Number(v)
    return Number.isFinite(n) ? n : 0
  }
  return 0
}

function formatAmount2(v: number): string {
  if (!Number.isFinite(v)) return '0.00'
  return v.toFixed(2)
}

type LabelsLod = 'off' | 'selection' | 'neighbors'
type Quality = 'low' | 'med' | 'high'

const labelsLod = ref<LabelsLod>('selection')
const quality = ref<Quality>('high')

const dprClamp = computed(() => {
  if (isTestMode.value) return 1
  if (quality.value === 'low') return 1
  if (quality.value === 'med') return 1.5
  return 2
})

const showDemoControls = computed(() => !isTestMode.value && (scene.value === 'D' || scene.value === 'E'))
const showResetView = computed(() => !isTestMode.value)

type LayoutNode = GraphNode & { __x: number; __y: number }
type LayoutLink = GraphLink & { __key: string }

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

function clamp01(v: number) {
  return Math.max(0, Math.min(1, v))
}

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v))
}

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
const pushFloatingLabel = overlayState.pushFloatingLabel
const pruneFloatingLabels = overlayState.pruneFloatingLabels
const floatingLabelsView = overlayState.floatingLabelsView
const resetOverlays = overlayState.resetOverlays

function scheduleTimeout(fn: () => void, delayMs: number) {
  return timers.schedule(fn, delayMs)
}

function clearScheduledTimeouts() {
  timers.clearAll()
}

function assertPlaylistEdgesExistInSnapshot(opts: { snapshot: GraphSnapshot; events: DemoEvent[]; eventsPath: string }) {
  const { snapshot, events, eventsPath } = opts
  const ok = new Set(snapshot.links.map((l) => keyEdge(l.source, l.target)))

  const assertEdge = (from: string, to: string, ctx: string) => {
    const k = keyEdge(from, to)
    if (!ok.has(k)) {
      throw new Error(`Unknown edge '${k}' referenced by ${ctx} (${eventsPath})`)
    }
  }

  for (let ei = 0; ei < events.length; ei++) {
    const evt = events[ei]!
    const baseCtx = `event[${ei}] ${evt.type} ${'event_id' in evt ? String((evt as any).event_id ?? '') : ''}`.trim()

    if (evt.type === 'tx.updated') {
      for (let i = 0; i < evt.edges.length; i++) {
        const e = evt.edges[i]!
        assertEdge(e.from, e.to, `${baseCtx} edges[${i}]`)
      }
      continue
    }

    if (evt.type === 'clearing.plan') {
      for (let si = 0; si < evt.steps.length; si++) {
        const step = evt.steps[si]!
        const he = step.highlight_edges ?? []
        const pe = step.particles_edges ?? []
        for (let i = 0; i < he.length; i++) {
          const e = he[i]!
          assertEdge(e.from, e.to, `${baseCtx} steps[${si}].highlight_edges[${i}]`)
        }
        for (let i = 0; i < pe.length; i++) {
          const e = pe[i]!
          assertEdge(e.from, e.to, `${baseCtx} steps[${si}].particles_edges[${i}]`)
        }
      }
    }
  }
}

function computeLayout(snapshot: GraphSnapshot, w: number, h: number, mode: LayoutMode) {
  const result = computeLayoutForMode(snapshot, w, h, mode, isTestMode.value)
  layout.nodes = result.nodes
  layout.links = result.links

  baselineLayoutPos.clear()
  for (const n of result.nodes) baselineLayoutPos.set(n.id, { x: n.__x, y: n.__y })

  // Re-apply pinned positions after any relayout.
  for (const [id, p] of pinnedPos) {
    const n = layout.nodes.find((x) => x.id === id)
    if (!n) continue
    n.__x = p.x
    n.__y = p.y
  }
}

const demoPlayer = useDemoPlayer({
  applyPatches: applyPatchesFromEvent,
  spawnSparks: (opts) => spawnSparks(fxState, opts),
  spawnNodeBursts: (opts) => spawnNodeBursts(fxState, opts),
  spawnEdgePulses: (opts) => spawnEdgePulses(fxState, opts),
  pushFloatingLabel,
  resetOverlays,
  fxColorForNode,
  addActiveEdge: (k) => activeEdges.add(k),
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
    out.push({
      id,
      x: ln.__x,
      y: ln.__y,
      text: gn?.name ? String(gn.name) : id,
      color: fxColorForNode(id, '#e2e8f0'),
    })
  }
  return out
})

const layoutNodeMap = computed(() => {
  const m = new Map<string, LayoutNode>()
  for (const n of layout.nodes) m.set(n.id, n)
  return m
})

const layoutLinkMap = computed(() => {
  const m = new Map<string, LayoutLink>()
  for (const l of layout.links) m.set(l.__key, l)
  return m
})

const selectedIncidentEdgeKeys = computed(() => {
  const out = new Set<string>()
  const id = state.selectedNodeId
  if (!id) return out
  for (const l of layout.links) {
    if (l.source === id || l.target === id) out.add(l.__key)
  }
  return out
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

function applyNodePatches(patches: NodePatch[] | undefined) {
  if (!patches?.length || !state.snapshot) return

  const snapIdx = new Map(state.snapshot.nodes.map((n, i) => [n.id, i]))
  const layoutIdx = new Map(layout.nodes.map((n, i) => [n.id, i]))

  for (const p of patches) {
    const si = snapIdx.get(p.id)
    if (si !== undefined) {
      const cur = state.snapshot.nodes[si]!
      state.snapshot.nodes[si] = {
        ...cur,
        net_balance_atoms: p.net_balance_atoms !== undefined ? p.net_balance_atoms : cur.net_balance_atoms,
        net_sign: p.net_sign !== undefined ? p.net_sign : cur.net_sign,
        viz_color_key: p.viz_color_key !== undefined ? p.viz_color_key : cur.viz_color_key,
        viz_size: p.viz_size !== undefined ? p.viz_size : cur.viz_size,
      }
    }

    const li = layoutIdx.get(p.id)
    if (li !== undefined) {
      const cur = layout.nodes[li]!
      layout.nodes[li] = {
        ...cur,
        net_balance_atoms: p.net_balance_atoms !== undefined ? p.net_balance_atoms : cur.net_balance_atoms,
        net_sign: p.net_sign !== undefined ? p.net_sign : cur.net_sign,
        viz_color_key: p.viz_color_key !== undefined ? p.viz_color_key : cur.viz_color_key,
        viz_size: p.viz_size !== undefined ? p.viz_size : cur.viz_size,
      }
    }
  }
}

function applyEdgePatches(patches: EdgePatch[] | undefined) {
  if (!patches?.length || !state.snapshot) return

  const snapIdx = new Map(state.snapshot.links.map((l, i) => [keyEdge(l.source, l.target), i]))
  const layoutIdx = new Map(layout.links.map((l, i) => [keyEdge(l.source, l.target), i]))

  for (const p of patches) {
    const k = keyEdge(p.source, p.target)

    const si = snapIdx.get(k)
    if (si !== undefined) {
      const cur = state.snapshot.links[si]!
      state.snapshot.links[si] = {
        ...cur,
        used: p.used !== undefined ? p.used : cur.used,
        available: p.available !== undefined ? p.available : cur.available,
        viz_color_key: p.viz_color_key !== undefined ? p.viz_color_key : cur.viz_color_key,
        viz_width_key: p.viz_width_key !== undefined ? p.viz_width_key : cur.viz_width_key,
        viz_alpha_key: p.viz_alpha_key !== undefined ? p.viz_alpha_key : cur.viz_alpha_key,
      }
    }

    const li = layoutIdx.get(k)
    if (li !== undefined) {
      const cur = layout.links[li]!
      layout.links[li] = {
        ...cur,
        used: p.used !== undefined ? p.used : cur.used,
        available: p.available !== undefined ? p.available : cur.available,
        viz_color_key: p.viz_color_key !== undefined ? p.viz_color_key : cur.viz_color_key,
        viz_width_key: p.viz_width_key !== undefined ? p.viz_width_key : cur.viz_width_key,
        viz_alpha_key: p.viz_alpha_key !== undefined ? p.viz_alpha_key : cur.viz_alpha_key,
      }
    }
  }
}

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
  try {
    const v = String(localStorage.getItem('geo.sim.layoutMode') ?? '')
    if (
      v === 'admin-force' ||
      v === 'community-clusters' ||
      v === 'balance-split' ||
      v === 'type-split' ||
      v === 'status-split'
    ) {
      layoutMode.value = v
    }
  } catch {
    // ignore
  }
})


onMounted(() => {
  try {
    const q = String(localStorage.getItem('geo.sim.quality') ?? '')
    if (q === 'low' || q === 'med' || q === 'high') quality.value = q
  } catch {
    // ignore
  }
  try {
    const l = String(localStorage.getItem('geo.sim.labelsLod') ?? '')
    if (l === 'off' || l === 'selection' || l === 'neighbors') labelsLod.value = l
  } catch {
    // ignore
  }
})

onMounted(() => {
  // Dev-only hook for quick runtime sanity checks (does not affect rendering).
  if (!import.meta.env.DEV) return
  ;(window as any).__geoSim = {
    get isTestMode() {
      return isTestMode.value
    },
    get isWebDriver() {
      return isWebDriver
    },
    get loading() {
      return state.loading
    },
    get error() {
      return state.error
    },
    get hasSnapshot() {
      return !!state.snapshot
    },
    fxState,
    runTxOnce,
    runClearingOnce,
  }
})

watch(layoutMode, () => {
  try {
    localStorage.setItem('geo.sim.layoutMode', layoutMode.value)
  } catch {
    // ignore
  }
})

watch(quality, () => {
  try {
    localStorage.setItem('geo.sim.quality', quality.value)
  } catch {
    // ignore
  }
  requestResizeAndLayout()
})

watch(labelsLod, () => {
  try {
    localStorage.setItem('geo.sim.labelsLod', labelsLod.value)
  } catch {
    // ignore
  }
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
  pruneFloatingLabels,
  drawBaseGraph,
  renderFxFrame,
  mapping: VIZ_MAPPING,
  fxState,
  getSelectedNodeId: () => state.selectedNodeId,
  activeEdges,
})

const ensureRenderLoop = renderLoop.ensureRenderLoop
const stopRenderLoop = renderLoop.stopRenderLoop
const renderOnce = renderLoop.renderOnce

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
  const hit = pickNodeAt(ev.clientX, ev.clientY)
  if (hit) {
    state.selectedNodeId = hit.id
    clearHoveredEdge()

    const ln = layout.nodes.find((n) => n.id === hit.id)
    if (ln && canvasEl.value) {
      dragState.active = true
      dragState.dragging = false
      dragState.nodeId = hit.id
      dragState.pointerId = ev.pointerId
      dragState.startClientX = ev.clientX
      dragState.startClientY = ev.clientY
      dragState.cachedNode = ln // Cache reference to avoid O(n) lookup on every pointermove

      const host = hostEl.value
      if (host) {
        const r = host.getBoundingClientRect()
        dragState.hostLeft = r.left
        dragState.hostTop = r.top
      } else {
        dragState.hostLeft = 0
        dragState.hostTop = 0
      }

      const sx = ev.clientX - dragState.hostLeft
      const sy = ev.clientY - dragState.hostTop
      const p = screenToWorld(sx, sy)
      dragState.offsetX = ln.__x - p.x
      dragState.offsetY = ln.__y - p.y

      try {
        canvasEl.value.setPointerCapture(ev.pointerId)
      } catch {
        // ignore
      }
    }

    return
  }

  cameraSystem.onPointerDown(ev)
}

const dragState = reactive({
  active: false,
  dragging: false,
  nodeId: null as string | null,
  pointerId: null as number | null,
  startClientX: 0,
  startClientY: 0,
  offsetX: 0,
  offsetY: 0,
  hostLeft: 0,
  hostTop: 0,
  // Cached reference to avoid O(n) lookup on every pointermove
  cachedNode: null as LayoutNode | null,
})

function onCanvasPointerMove(ev: PointerEvent) {
  if (dragState.active && dragState.pointerId === ev.pointerId && dragState.cachedNode) {
    const dx = ev.clientX - dragState.startClientX
    const dy = ev.clientY - dragState.startClientY
    const dist2 = dx * dx + dy * dy
    const threshold2 = 4 * 4

    if (!dragState.dragging && dist2 < threshold2) return

    dragState.dragging = true
    clearHoveredEdge()

    // Use cached node reference for O(1) access instead of O(n) find()
    const ln = dragState.cachedNode

    ensureRenderLoop()

    const sx = ev.clientX - dragState.hostLeft
    const sy = ev.clientY - dragState.hostTop
    const p = screenToWorld(sx, sy)
    const x = p.x + dragState.offsetX
    const y = p.y + dragState.offsetY

    ln.__x = x
    ln.__y = y
    // Dragging is an ephemeral override. Persist it only when the node is pinned.
    // This keeps the Pin button meaningful: after dragging you can choose to Pin.
    const id = dragState.nodeId
    if (id && pinnedPos.has(id)) {
      pinnedPos.set(id, { x, y })
    }

    // Force immediate redraw to keep drag responsive even when the RAF loop is idle.
    renderOnce()
    return
  }

  // Hover only when a node is selected, so it's obvious which edges are relevant.
  // Also keep WebDriver runs stable (no transient tooltip).
  if (!panState.active && !isWebDriver && state.selectedNodeId && selectedIncidentEdgeKeys.value.size > 0) {
    // Do not show edge tooltips when hovering a node.
    const nodeHit = pickNodeAt(ev.clientX, ev.clientY)
    if (nodeHit) {
      clearHoveredEdge()
      return
    }

    const seg = pickEdgeAt(ev.clientX, ev.clientY)
    if (seg && (seg.fromId === state.selectedNodeId || seg.toId === state.selectedNodeId)) {
      const link = layoutLinkMap.value.get(seg.key)
      const s = clientToScreen(ev.clientX, ev.clientY)
      const p = screenToWorld(s.x, s.y)
      const cp = closestPointOnSegment(p.x, p.y, seg.ax, seg.ay, seg.bx, seg.by)
      const sp = worldToScreen(cp.x, cp.y)

      hoveredEdge.key = seg.key
      hoveredEdge.fromId = seg.fromId
      hoveredEdge.toId = seg.toId
      hoveredEdge.amountText = formatEdgeAmountText(link)
      hoveredEdge.screenX = sp.x
      hoveredEdge.screenY = sp.y
    } else {
      clearHoveredEdge()
    }
  } else if (!panState.active) {
    clearHoveredEdge()
  }

  if (!panState.active) return

  cameraSystem.onPointerMove(ev)
}

function onCanvasPointerUp(ev: PointerEvent) {
  if (dragState.active && dragState.pointerId === ev.pointerId) {
    dragState.active = false
    dragState.dragging = false
    dragState.nodeId = null
    dragState.pointerId = null
    dragState.hostLeft = 0
    dragState.hostTop = 0
    dragState.cachedNode = null // Clear cached reference

    try {
      canvasEl.value?.releasePointerCapture(ev.pointerId)
    } catch {
      // ignore
    }
    return
  }

  const wasClick = cameraSystem.onPointerUp(ev)
  if (!wasClick) return

  // Click on empty background: clear selection.
  state.selectedNodeId = null
  clearHoveredEdge()
}

const isSelectedPinned = computed(() => {
  const id = state.selectedNodeId
  if (!id) return false
  return pinnedPos.has(id)
})

function pinSelectedNode() {
  const id = state.selectedNodeId
  if (!id) return
  const ln = layout.nodes.find((n) => n.id === id)
  if (!ln) return
  pinnedPos.set(id, { x: ln.__x, y: ln.__y })
}

function unpinSelectedNode() {
  const id = state.selectedNodeId
  if (!id) return
  pinnedPos.delete(id)
  const base = baselineLayoutPos.get(id)
  const ln = layout.nodes.find((n) => n.id === id)
  if (ln && base) {
    ln.__x = base.x
    ln.__y = base.y
  }
}

function onCanvasWheel(ev: WheelEvent) {
  cameraSystem.onWheel(ev)
}

function resetView() {
  resetCamera()
  clampCameraPan()
}

function runTxEvent(evt: TxUpdatedEvent, opts?: { onFinished?: () => void }) {
  if (!state.snapshot) return
  demoPlayer.runTxEvent(evt, opts)
}

async function runTxOnce() {
  if (!state.snapshot) return

  stopPlaylistPlayback()

  state.error = ''

  if (import.meta.env.DEV) {
    ;(window as any).__geoSimTxCalls = ((window as any).__geoSimTxCalls ?? 0) + 1
  }

  try {
    ensureRenderLoop()

    clearScheduledTimeouts()
    resetOverlays()

    let evt: TxUpdatedEvent | undefined = state.demoTxEvents[0]
    if (!evt) {
      const { events, sourcePath: eventsPath } = await loadEvents(effectiveEq.value, 'demo-tx')
      assertPlaylistEdgesExistInSnapshot({ snapshot: state.snapshot, events, eventsPath })
      evt = events.find((e): e is TxUpdatedEvent => e.type === 'tx.updated')
    }
    if (!evt) return

    runTxEvent(evt)
  } catch (e: any) {
    const msg = String(e?.message ?? e)
    state.error = msg
    if (import.meta.env.DEV) {
      ;(window as any).__geoSimLastTxError = msg
    }
    // eslint-disable-next-line no-console
    console.error(e)
  }
}

async function runClearingOnce() {
  if (!state.snapshot) return

  stopPlaylistPlayback()
  state.error = ''

  try {
    ensureRenderLoop()
    clearScheduledTimeouts()
    resetOverlays()

    let plan = state.demoClearingPlan
    let done = state.demoClearingDone

    if (!plan) {
      const { events, sourcePath: eventsPath } = await loadEvents(effectiveEq.value, 'demo-clearing')
      assertPlaylistEdgesExistInSnapshot({ snapshot: state.snapshot, events, eventsPath })
      plan = events.find((e): e is ClearingPlanEvent => e.type === 'clearing.plan') ?? null
      done = events.find((e): e is ClearingDoneEvent => e.type === 'clearing.done') ?? null
    }
    if (!plan) return

    demoPlayer.runClearingOnce(plan, done)
  } catch (e: any) {
    const msg = String(e?.message ?? e)
    state.error = msg
    if (import.meta.env.DEV) {
      ;(window as any).__geoSimLastClearingError = msg
    }
    // eslint-disable-next-line no-console
    console.error(e)
  }
}

const canDemoPlay = computed(() => {
  if (scene.value === 'D') return state.demoTxEvents.length > 0
  if (scene.value === 'E') return !!state.demoClearingPlan
  return false
})

const demoPlayLabel = computed(() => (playlist.playing ? 'Pause' : 'Play'))

function runClearingStep(stepIndex: number, opts?: { onFinished?: () => void }) {
  const plan = state.demoClearingPlan
  if (!plan) return
  if (!state.snapshot) return
  demoPlayer.runClearingStep(stepIndex, plan, state.demoClearingDone, opts)
}

function demoStepOnce() {
  if (!state.snapshot) return
  if (!canDemoPlay.value) return
  demoPlayer.demoStepOnce(scene.value, state.demoTxEvents, state.demoClearingPlan, state.demoClearingDone)
}

function demoTogglePlay() {
  if (!state.snapshot) return
  if (!canDemoPlay.value) return
  demoPlayer.demoTogglePlay(scene.value, state.demoTxEvents, state.demoClearingPlan, state.demoClearingDone)
}

function demoReset() {
  resetDemoState()
}

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

    <div v-if="hoveredEdge.key" class="edge-tooltip" :style="edgeTooltipStyle()" aria-label="Edge tooltip">
      <div class="edge-tooltip-title">
        {{ getNodeById(hoveredEdge.fromId)?.name ?? hoveredEdge.fromId }} → {{ getNodeById(hoveredEdge.toId)?.name ?? hoveredEdge.toId }}
      </div>
      <div class="edge-tooltip-amount mono">{{ hoveredEdge.amountText }}</div>
    </div>

    <!-- Minimal top HUD (controls + small status) -->
    <div class="hud-top">
      <div class="hud-row">
        <div class="pill">
          <span class="label">EQ</span>
          <template v-if="isDemoFixtures">
            <span class="value">UAH</span>
          </template>
          <template v-else>
            <select v-model="eq" class="select" aria-label="Equivalent">
              <option value="UAH">UAH</option>
              <option value="HOUR">HOUR</option>
              <option value="EUR">EUR</option>
            </select>
          </template>
        </div>

        <div class="field">
          <span class="label">Layout</span>
          <select v-model="layoutMode" class="select" aria-label="Layout">
            <option value="admin-force">Organic cloud (links)</option>
            <option value="community-clusters">Community clusters</option>
            <option value="balance-split">Constellations: balance</option>
            <option value="type-split">Constellations: type</option>
            <option value="status-split">Constellations: status</option>
          </select>
        </div>

        <div class="pill">
          <span class="label">Scene</span>
          <select v-model="scene" class="select" aria-label="Scene">
            <option value="A">A — Overview</option>
            <option value="B">B — Focus</option>
            <option value="C">C — Statuses</option>
            <option value="D">D — Tx burst</option>
            <option value="E">E — Clearing</option>
          </select>
        </div>

        <div v-if="state.snapshot" class="pill subtle" aria-label="Stats">
          <span class="mono">Nodes {{ state.snapshot.nodes.length }} | Links {{ state.snapshot.links.length }}</span>
        </div>

        <div v-if="isTestMode && !isWebDriver" class="pill subtle" aria-label="Mode">
          <span class="mono">TEST MODE</span>
        </div>
      </div>
    </div>

    <!-- Node card -->
    <div v-if="selectedNode && !dragState.active" class="node-card" :style="nodeCardStyle()">
      <div class="node-header">
        <div class="node-title">{{ selectedNode.name ?? selectedNode.id }}</div>
        <div v-if="!isTestMode && !isWebDriver" class="node-actions">
          <button v-if="!isSelectedPinned" class="btn btn-ghost btn-xxs" type="button" @click="pinSelectedNode">Pin</button>
          <button v-else class="btn btn-ghost btn-xxs" type="button" @click="unpinSelectedNode">Unpin</button>
        </div>
      </div>

      <div class="node-grid">
        <div class="node-item">
          <span class="k">Type</span>
          <span class="v">{{ selectedNode.type ?? '—' }}</span>
        </div>
        <div class="node-item">
          <span class="k">Out</span>
          <span class="v mono">{{ selectedNodeEdgeStats?.outLimitText ?? '—' }}</span>
          <span class="v">{{ state.snapshot?.equivalent ?? '' }}</span>
        </div>

        <div class="node-item">
          <span class="k">Status</span>
          <span class="v">{{ selectedNode.status ?? '—' }}</span>
        </div>
        <div class="node-item">
          <span class="k">In</span>
          <span class="v mono">{{ selectedNodeEdgeStats?.inLimitText ?? '—' }}</span>
          <span class="v">{{ state.snapshot?.equivalent ?? '' }}</span>
        </div>

        <div class="node-item">
          <span class="k">Net</span>
          <span class="v mono">{{ selectedNode.net_balance_atoms ?? '—' }}</span>
        </div>
        <div class="node-item">
          <span class="k">Degree</span>
          <span class="v mono">{{ selectedNodeEdgeStats?.degree ?? '—' }}</span>
        </div>
      </div>
    </div>

    <!-- Bottom HUD (as per prototypes: minimal buttons) -->
    <div class="hud-bottom">
      <button class="btn" type="button" @click="runTxOnce">Single Tx</button>
      <button class="btn" type="button" @click="runClearingOnce">Run Clearing</button>

      <template v-if="showResetView">
        <div class="hud-divider" />
        <button class="btn btn-ghost" type="button" @click="resetView">Reset view</button>
      </template>

      <template v-if="showDemoControls">
        <div class="hud-divider" />
        <button class="btn btn-ghost" type="button" :disabled="playlist.playing" @click="demoStepOnce">Step</button>
        <button class="btn" type="button" :disabled="!canDemoPlay" @click="demoTogglePlay">{{ demoPlayLabel }}</button>
        <button class="btn btn-ghost" type="button" @click="demoReset">Reset</button>

        <div class="pill subtle">
          <span class="label">Quality</span>
          <select v-model="quality" class="select select-compact" aria-label="Quality">
            <option value="low">Low</option>
            <option value="med">Med</option>
            <option value="high">High</option>
          </select>
        </div>

        <div class="pill subtle">
          <span class="label">Labels</span>
          <select v-model="labelsLod" class="select select-compact" aria-label="Labels">
            <option value="off">Off</option>
            <option value="selection">Selection</option>
            <option value="neighbors">Neighbors</option>
          </select>
        </div>
      </template>
    </div>

    <!-- Loading / error overlay (fail-fast, but non-intrusive) -->
    <div v-if="state.loading" class="overlay">Loading fixtures…</div>
    <div v-else-if="state.error" class="overlay overlay-error">
      <div class="overlay-title">Fixtures error</div>
      <div class="overlay-text mono">{{ state.error }}</div>
    </div>

    <!-- Floating Labels (isolated layer for perf) -->
    <div v-if="labelNodes.length" class="labels-layer">
      <div
        v-for="n in labelNodes"
        :key="n.id"
        class="node-label"
        :style="{ transform: worldToCssTranslate(n.x, n.y) }"
      >
        <div class="node-label-inner" :style="{ borderColor: n.color, color: n.color }">{{ n.text }}</div>
      </div>
    </div>

    <div class="floating-layer">
      <div
        v-for="fl in floatingLabelsView"
        :key="fl.id"
        class="floating-label"
        :style="{ transform: worldToCssTranslate(fl.x, fl.y) }"
      >
        <div class="floating-label-inner" :style="{ color: fl.color }">{{ fl.text }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
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
  z-index: 0;
}

.hud-top {
  position: absolute;
  top: 12px;
  left: 12px;
  right: 12px;
  pointer-events: none;
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
  outline: none;
  font-size: 12px;
}

.value {
  color: rgba(226, 232, 240, 0.95);
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
  transform: translate3d(-50%, -135%, 0);
  font-size: 11px;
  font-weight: 650;
  padding: 4px 8px;
  border-radius: 999px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  background: rgba(2, 6, 23, 0.62);
  text-shadow: 0 0 10px currentColor;
  white-space: nowrap;
}

.floating-label {
  position: absolute;
  left: 0;
  top: 0;
  pointer-events: none;
}

.floating-label-inner {
  font-size: 14px;
  font-weight: 700;
  text-shadow: 0 0 10px currentColor; /* Glow */
  animation: floatUpFade 2.6s cubic-bezier(0.16, 1, 0.3, 1) forwards;
  /* Keep centering constant; animate only a pixel offset for smooth motion. */
  transform: translate3d(-50%, -50%, 0) translate3d(0, 0, 0);
  will-change: transform, opacity;
  backface-visibility: hidden;
  white-space: pre-line;
  text-align: center;
  line-height: 1.05;
}

@keyframes floatUpFade {
  0% {
    opacity: 0;
    transform: translate3d(-50%, -50%, 0) translate3d(0, 0, 0);
  }
  8% {
    opacity: 1;
    transform: translate3d(-50%, -50%, 0) translate3d(0, -2px, 0);
  }
  60% {
    opacity: 1;
    transform: translate3d(-50%, -50%, 0) translate3d(0, -14px, 0);
  }
  100% {
    opacity: 0;
    transform: translate3d(-50%, -50%, 0) translate3d(0, -34px, 0);
  }
}
</style>
