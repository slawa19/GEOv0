import type { ComputedRef, Ref } from 'vue'
import type { ClearingDoneEvent, ClearingPlanEvent, DemoEvent, GraphSnapshot, TxUpdatedEvent } from '../types'
import type { LayoutMode } from '../layout/forceLayout'
import type { SceneId } from '../scenes'
import { useSceneState } from './useSceneState'

type AppState = {
  loading: boolean
  error: string
  sourcePath: string
  eventsPath: string
  snapshot: GraphSnapshot | null
  selectedNodeId: string | null
  flash: number
  demoTxEvents: TxUpdatedEvent[]
  demoClearingPlan: ClearingPlanEvent | null
  demoClearingDone: ClearingDoneEvent | null
}

type LoadEventsKind = 'demo-tx' | 'demo-clearing'

export function useAppSceneState(opts: {
  eq: Ref<string>
  scene: Ref<SceneId>
  layoutMode: Ref<LayoutMode>
  isTestMode: () => boolean
  isEqAllowed: (eq: string) => boolean
  effectiveEq: ComputedRef<string>
  state: AppState
  loadSnapshot: (eq: string) => Promise<{ snapshot: GraphSnapshot; sourcePath: string }>
  loadEvents: (eq: string, kind: LoadEventsKind) => Promise<{ events: DemoEvent[]; sourcePath: string }>
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
}) {
  return useSceneState({
    eq: opts.eq,
    scene: opts.scene,
    layoutMode: opts.layoutMode,
    allowEqDeepLink: () => !opts.isTestMode(),
    isEqAllowed: opts.isEqAllowed,
    effectiveEq: opts.effectiveEq,
    state: opts.state,
    loadSnapshot: opts.loadSnapshot,
    loadEvents: opts.loadEvents,
    assertPlaylistEdgesExistInSnapshot: opts.assertPlaylistEdgesExistInSnapshot,
    clearScheduledTimeouts: opts.clearScheduledTimeouts,
    resetPlaylistPointers: opts.resetPlaylistPointers,
    resetCamera: opts.resetCamera,
    resetLayoutKeyCache: opts.resetLayoutKeyCache,
    resetOverlays: opts.resetOverlays,
    resizeAndLayout: opts.resizeAndLayout,
    ensureRenderLoop: opts.ensureRenderLoop,
    setupResizeListener: opts.setupResizeListener,
    teardownResizeListener: opts.teardownResizeListener,
    stopRenderLoop: opts.stopRenderLoop,
  })
}
