import type { ComputedRef, Ref } from 'vue'
import { watch } from 'vue'
import type { LayoutMode } from '../layout/forceLayout'
import type { GraphSnapshot } from '../types'

type SceneId = 'A' | 'B' | 'C'

type LoadSnapshotFn = (eq: string) => Promise<{ snapshot: GraphSnapshot; sourcePath: string }>
type RecoverySceneContext = {
  runId: string
  equivalent: string
  isCurrent: () => boolean
}
type LoadRecoverySnapshotFn = (context: Pick<RecoverySceneContext, 'runId' | 'equivalent'>) => Promise<{
  snapshot: GraphSnapshot
  sourcePath: string
}>

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
  loadRecoverySnapshot?: LoadRecoverySnapshotFn

  clearScheduledTimeouts: (opts?: { keepCritical?: boolean }) => void
  resetCamera: () => void
  resetLayoutKeyCache: () => void
  resetOverlays: () => void

  resizeAndLayout: () => void
  ensureRenderLoop: () => void

  // Optional hook: when a new snapshot is loaded with the same node IDs as the current snapshot,
  // we may skip full relayout/camera resets. In that case, the app may still need to sync
  // visual/metrics fields into the layout node/link objects used for rendering.
  onIncrementalSnapshotLoaded?: (snapshot: import('../types').GraphSnapshot) => void

  setupResizeListener: () => void
  teardownResizeListener: () => void
  stopRenderLoop: () => void

  // When true, setup() skips the initial loadScene() call.
  // Useful when another subsystem (e.g. real-mode boot) is responsible for the first load.
  skipInitialLoad?: () => boolean
  // A scene/equivalent watcher supersedes any replay-recovery snapshot that
  // might currently own the SSE lifecycle.
  onSceneContextChange?: () => Promise<void>
}

type UseSceneStateReturn = {
  loadScene: () => Promise<void>
  loadRecoveryScene: (context: RecoverySceneContext) => Promise<boolean>
  setup: () => void
  teardown: () => void
}

export function useSceneState(deps: UseSceneStateDeps): UseSceneStateReturn {
  let deepLinkFocusNodeId: string | null = null
  let loadSceneSeq = 0

  // Track loaded snapshot identity to detect "incremental" updates vs. full scene changes.
  // When node IDs are the same, we can skip costly resets (camera, overlays, layout animation).
  let lastSnapshotNodeIds: Set<string> | null = null
  let hasLoadedOnce = false

  function getErrorMessage(error: unknown): string {
    if (error instanceof Error) return error.message
    return String(error)
  }

  function parseSceneContext(sourcePath: string | null | undefined): { kind: 'scenario' | 'run' | 'other'; id: string } {
    const s = String(sourcePath ?? '').trim()
    if (!s) return { kind: 'other', id: '' }

    // We intentionally parse from the debug `sourcePath` string (not from app state),
    // because `loadSnapshot()` already knows the effective source of truth.
    // Examples:
    // - GET http://.../simulator/scenarios/<id>/graph/preview?... -> scenario:<id>
    // - GET http://.../simulator/runs/<id>/graph/snapshot?...     -> run:<id>
    const mScenario = s.match(/\/simulator\/scenarios\/([^/\s?]+)\/graph\/preview/i)
    if (mScenario && mScenario[1]) return { kind: 'scenario', id: decodeURIComponent(mScenario[1]) }

    const mRun = s.match(/\/simulator\/runs\/([^/\s?]+)\/graph\/snapshot/i)
    if (mRun && mRun[1]) return { kind: 'run', id: decodeURIComponent(mRun[1]) }

    return { kind: 'other', id: s }
  }

  function shouldTreatAsIncrementalUpdate(opts: {
    prevSourcePath: string
    nextSourcePath: string
    nodeIdsMatch: boolean
  }): boolean {
    if (!opts.nodeIdsMatch) return false

    const prev = parseSceneContext(opts.prevSourcePath)
    const next = parseSceneContext(opts.nextSourcePath)

    // Default: keep prior behavior when we can't confidently infer context.
    if (prev.kind === 'other' || next.kind === 'other') return true

    // Same context: incremental.
    if (prev.kind === next.kind && prev.id === next.id) return true

    // Smooth transition preview -> run (avoid camera/layout reset when startRun begins).
    if (prev.kind === 'scenario' && next.kind === 'run') return true

    // Scenario switching (preview -> preview with different id) must be treated as a full scene change
    // to ensure layout + overlays reflect the new scenario deterministically.
    return false
  }

  function snapshotNodeIdsMatch(oldIds: Set<string> | null, snapshot: GraphSnapshot): boolean {
    if (!oldIds) return false
    if (oldIds.size !== snapshot.nodes.length) return false
    for (const n of snapshot.nodes) {
      if (!oldIds.has(n.id)) return false
    }
    return true
  }

  async function applySceneLoad(input: {
    loadSnapshot: () => Promise<{ snapshot: GraphSnapshot; sourcePath: string }>
    isContextCurrent?: () => boolean
    recoveryContext?: Pick<RecoverySceneContext, 'runId' | 'equivalent'>
    rethrow?: boolean
  }): Promise<boolean> {
    if (input.isContextCurrent && !input.isContextCurrent()) return false
    const mySeq = ++loadSceneSeq
    const isCurrent = () => loadSceneSeq === mySeq && (!input.isContextCurrent || input.isContextCurrent())

    // IMPORTANT:
    // - `loadScene()` can be triggered by snapshot refreshes within the same run.
    // - We must NOT silently drop pending critical timers (e.g. delayed receiver amount labels).
    // Therefore we start by clearing only non-critical timers.
    deps.clearScheduledTimeouts({ keepCritical: true })

    deps.state.loading = true
    deps.state.error = ''

    try {
      const { snapshot, sourcePath } = await input.loadSnapshot()
      if (!isCurrent()) return false

      if (input.recoveryContext) {
        const source = parseSceneContext(sourcePath)
        const expectedEq = String(input.recoveryContext.equivalent || '').trim().toUpperCase()
        const snapshotEq = String(snapshot.equivalent || '').trim().toUpperCase()
        if (source.kind !== 'run' || source.id !== input.recoveryContext.runId || snapshotEq !== expectedEq) {
          throw new Error('Replay recovery snapshot does not match the active run/equivalent context')
        }
      }

      // Detect if this is an "incremental" update (same node IDs) vs. a full scene change.
      // Incremental: skip camera reset, overlay clear, and layout cache invalidation.
      // This prevents "explosion" animations when transitioning preview → run with the same graph.
      const nodeIdsMatch = snapshotNodeIdsMatch(lastSnapshotNodeIds, snapshot)
      const isIncrementalUpdate =
        hasLoadedOnce &&
        deps.state.snapshot !== null &&
        shouldTreatAsIncrementalUpdate({
          prevSourcePath: deps.state.sourcePath,
          nextSourcePath: sourcePath,
          nodeIdsMatch,
        })

      // Only treat *node composition* changes as structural.
      // Link count can differ between preview → run (or between run snapshots) while we still
      // want to keep camera + node positions stable to avoid a visible "graph jump".
      const isStructuralChange = !isIncrementalUpdate || deps.state.snapshot?.nodes.length !== snapshot.nodes.length

      if (!isIncrementalUpdate) {
        // Full reload: clear everything (including critical timers) because we're changing scene context.
        deps.clearScheduledTimeouts()
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
      return true
    } catch (error) {
      if (!isCurrent()) return false
      deps.state.error = getErrorMessage(error)
      if (input.rethrow) throw error
      return false
    } finally {
      if (isCurrent()) deps.state.loading = false
    }
  }

  async function loadScene() {
    await applySceneLoad({
      loadSnapshot: () => deps.loadSnapshot(deps.effectiveEq.value),
    })
  }

  async function loadRecoveryScene(context: RecoverySceneContext): Promise<boolean> {
    if (!deps.loadRecoverySnapshot) throw new Error('Strict replay recovery snapshot loader is not configured')
    const runId = String(context.runId || '').trim()
    const equivalent = String(context.equivalent || '').trim().toUpperCase()
    if (!runId || !equivalent) throw new Error('Replay recovery requires a run id and equivalent')

    return await applySceneLoad({
      loadSnapshot: () => deps.loadRecoverySnapshot!({ runId, equivalent }),
      isContextCurrent: context.isCurrent,
      recoveryContext: { runId, equivalent },
      rethrow: true,
    })
  }

  watch([deps.eq, deps.scene], () => {
    if (deps.onSceneContextChange) {
      void deps.onSceneContextChange()
      return
    }
    void loadScene()
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

    // In real mode, the initial snapshot load is handled by useSimulatorRealMode's immediate watcher.
    // Calling loadScene() here would create a duplicate/racing load, causing a visible "Loading…" flash.
    if (!deps.skipInitialLoad?.()) {
      loadScene()
    }
    deps.setupResizeListener()
  }

  function teardown() {
    loadSceneSeq += 1
    deps.state.loading = false
    deps.teardownResizeListener()
    deps.clearScheduledTimeouts()
    deps.stopRenderLoop()
  }

  return { loadScene, loadRecoveryScene, setup, teardown }
}
