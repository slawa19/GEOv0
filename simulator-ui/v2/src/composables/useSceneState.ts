import type { ComputedRef, Ref } from 'vue'
import { watch } from 'vue'
import type { ClearingDoneEvent, ClearingPlanEvent, DemoEvent, GraphSnapshot, TxUpdatedEvent } from '../types'
import type { LayoutMode } from '../layout/forceLayout'

type SceneId = 'A' | 'B' | 'C' | 'D' | 'E'

type LoadSnapshotFn = (eq: string) => Promise<{ snapshot: GraphSnapshot; sourcePath: string }>

type LoadEventsKind = 'demo-tx' | 'demo-clearing'

type LoadEventsFn = (eq: string, kind: LoadEventsKind) => Promise<{ events: DemoEvent[]; sourcePath: string }>

type SceneState = {
  loading: boolean
  error: string
  sourcePath: string
  eventsPath: string
  snapshot: GraphSnapshot | null
  demoTxEvents: TxUpdatedEvent[]
  demoClearingPlan: ClearingPlanEvent | null
  demoClearingDone: ClearingDoneEvent | null
  selectedNodeId: string | null
}

type UseSceneStateDeps = {
  eq: Ref<string>
  scene: Ref<SceneId>
  layoutMode: Ref<LayoutMode>

  allowEqDeepLink: () => boolean
  isEqAllowed: (eq: string) => boolean

  effectiveEq: ComputedRef<string>

  state: SceneState

  loadSnapshot: LoadSnapshotFn
  loadEvents: LoadEventsFn
  assertPlaylistEdgesExistInSnapshot: (opts: { snapshot: GraphSnapshot; events: DemoEvent[]; eventsPath: string }) => void

  clearScheduledTimeouts: () => void
  resetPlaylistPointers: () => void
  resetCamera: () => void
  resetLayoutKeyCache: () => void
  resetOverlays: () => void

  resizeAndLayout: () => void
  ensureRenderLoop: () => void

  setupResizeListener: () => void
  teardownResizeListener: () => void
  stopRenderLoop: () => void
}

type UseSceneStateReturn = {
  loadScene: () => Promise<void>
  setup: () => void
  teardown: () => void
}

export function useSceneState(deps: UseSceneStateDeps): UseSceneStateReturn {
  let deepLinkFocusNodeId: string | null = null

  // Track loaded snapshot identity to detect "incremental" updates vs. full scene changes.
  // When node IDs are the same, we can skip costly resets (camera, overlays, layout animation).
  let lastSnapshotNodeIds: Set<string> | null = null
  let hasLoadedOnce = false

  function snapshotNodeIdsMatch(oldIds: Set<string> | null, snapshot: GraphSnapshot): boolean {
    if (!oldIds) return false
    if (oldIds.size !== snapshot.nodes.length) return false
    for (const n of snapshot.nodes) {
      if (!oldIds.has(n.id)) return false
    }
    return true
  }

  async function loadScene() {
    // Only clear timers/pointers for a full reload.
    deps.clearScheduledTimeouts()
    deps.resetPlaylistPointers()

    deps.state.loading = true
    deps.state.error = ''

    try {
      const { snapshot, sourcePath } = await deps.loadSnapshot(deps.effectiveEq.value)

      // Detect if this is an "incremental" update (same node IDs) vs. a full scene change.
      // Incremental: skip camera reset, overlay clear, and layout cache invalidation.
      // This prevents "explosion" animations when transitioning preview → run with the same graph.
      const isIncrementalUpdate =
        hasLoadedOnce && deps.state.snapshot !== null && snapshotNodeIdsMatch(lastSnapshotNodeIds, snapshot)

      // Structural change = different node count or different link count (needs layout recalc).
      const isStructuralChange =
        !isIncrementalUpdate ||
        deps.state.snapshot?.nodes.length !== snapshot.nodes.length ||
        deps.state.snapshot?.links.length !== snapshot.links.length

      if (!isIncrementalUpdate) {
        deps.resetCamera()
        deps.state.sourcePath = ''
        deps.state.eventsPath = ''
        deps.state.snapshot = null
        deps.resetLayoutKeyCache()
        deps.state.demoTxEvents = []
        deps.state.demoClearingPlan = null
        deps.state.demoClearingDone = null
        deps.state.selectedNodeId = null
        deps.resetOverlays()
      }

      deps.state.snapshot = snapshot
      deps.state.sourcePath = sourcePath
      lastSnapshotNodeIds = new Set(snapshot.nodes.map((n) => n.id))
      hasLoadedOnce = true

      if (deepLinkFocusNodeId && snapshot.nodes.some((n) => n.id === deepLinkFocusNodeId)) {
        deps.state.selectedNodeId = deepLinkFocusNodeId
      }

      if (deps.scene.value === 'D') {
        const r = await deps.loadEvents(deps.effectiveEq.value, 'demo-tx')
        deps.state.eventsPath = r.sourcePath
        deps.assertPlaylistEdgesExistInSnapshot({ snapshot, events: r.events, eventsPath: r.sourcePath })
        deps.state.demoTxEvents = r.events.filter((e): e is TxUpdatedEvent => e.type === 'tx.updated')
      }

      if (deps.scene.value === 'E') {
        const r = await deps.loadEvents(deps.effectiveEq.value, 'demo-clearing')
        deps.state.eventsPath = r.sourcePath
        deps.assertPlaylistEdgesExistInSnapshot({ snapshot, events: r.events, eventsPath: r.sourcePath })
        deps.state.demoClearingPlan = r.events.find((e): e is ClearingPlanEvent => e.type === 'clearing.plan') ?? null
        deps.state.demoClearingDone = r.events.find((e): e is ClearingDoneEvent => e.type === 'clearing.done') ?? null
      }

      // Only run full layout (with physics animation) if structure changed or first load.
      if (isStructuralChange || !hasLoadedOnce) {
        deps.resizeAndLayout()
      }
      deps.ensureRenderLoop()
    } catch (e: any) {
      deps.state.error = String(e?.message ?? e)
    } finally {
      deps.state.loading = false
    }
  }

  watch([deps.eq, deps.scene], () => {
    loadScene()
  })

  function setup() {
    // Allow deterministic deep-links for e2e/visual tests.
    try {
      const params = new URLSearchParams(window.location.search)

      const eq = String(params.get('eq') ?? '').trim()
      if (eq && deps.allowEqDeepLink()) {
        const normalized = eq.toUpperCase()
        if (deps.isEqAllowed(normalized)) {
          deps.eq.value = normalized
        }
      }

      const s = String(params.get('scene') ?? '')
      if (s === 'A' || s === 'B' || s === 'C' || s === 'D' || s === 'E') {
        deps.scene.value = s
      }

      const lm = String(params.get('layout') ?? '')
      if (
        lm === 'admin-force' ||
        lm === 'community-clusters' ||
        lm === 'balance-split' ||
        lm === 'type-split' ||
        lm === 'status-split'
      ) {
        deps.layoutMode.value = lm
      }

      const focus = String(params.get('focus') ?? '').trim()
      deepLinkFocusNodeId = focus ? focus : null
    } catch {
      // ignore
    }

    loadScene()
    deps.setupResizeListener()
  }

  function teardown() {
    deps.teardownResizeListener()
    deps.clearScheduledTimeouts()
    deps.stopRenderLoop()
  }

  return { loadScene, setup, teardown }
}
