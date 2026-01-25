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

  async function loadScene() {
    deps.clearScheduledTimeouts()
    deps.resetPlaylistPointers()
    deps.resetCamera()
    deps.state.loading = true
    deps.state.error = ''
    deps.state.sourcePath = ''
    deps.state.eventsPath = ''
    deps.state.snapshot = null
    deps.resetLayoutKeyCache()
    deps.state.demoTxEvents = []
    deps.state.demoClearingPlan = null
    deps.state.demoClearingDone = null
    deps.state.selectedNodeId = null
    deps.resetOverlays()

    try {
      const { snapshot, sourcePath } = await deps.loadSnapshot(deps.effectiveEq.value)
      deps.state.snapshot = snapshot
      deps.state.sourcePath = sourcePath

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

      deps.resizeAndLayout()
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
