import type { ComputedRef, Ref } from 'vue'
import { watch } from 'vue'
import type { LayoutMode } from '../layout/forceLayout'
import type { GraphSnapshot } from '../types'

type SceneId = 'A' | 'B' | 'C'

type LoadSnapshotFn = (eq: string) => Promise<{ snapshot: GraphSnapshot; sourcePath: string }>

type SceneState = {
  loading: boolean
  error: string
  sourcePath: string
  snapshot: GraphSnapshot | null
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

  clearScheduledTimeouts: () => void
  resetCamera: () => void
  resetLayoutKeyCache: () => void
  resetOverlays: () => void

  resizeAndLayout: () => void
  ensureRenderLoop: () => void

  // Optional hook: when a new snapshot is loaded with the same node IDs as the current snapshot,
  // we may skip full relayout/camera resets. In that case, the app may still need to sync
  // visual/metrics fields into the layout node/link objects used for rendering.
  onIncrementalSnapshotLoaded?: (snapshot: GraphSnapshot) => void

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
        deps.state.snapshot = null
        deps.resetLayoutKeyCache()
        deps.state.selectedNodeId = null
        deps.resetOverlays()
      }

      deps.state.snapshot = snapshot
      deps.state.sourcePath = sourcePath
      lastSnapshotNodeIds = new Set(snapshot.nodes.map((n) => n.id))
      hasLoadedOnce = true

      if (isIncrementalUpdate) {
        deps.onIncrementalSnapshotLoaded?.(snapshot)
      }

      if (deepLinkFocusNodeId && snapshot.nodes.some((n) => n.id === deepLinkFocusNodeId)) {
        deps.state.selectedNodeId = deepLinkFocusNodeId
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
      if (s === 'A' || s === 'B' || s === 'C') {
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
