import type { ComputedRef, Ref } from 'vue'
import type { LayoutMode } from '../layout/forceLayout'
import type { SceneId } from '../scenes'
import type { SimulatorAppState } from '../types/simulatorApp'
import { useSceneState } from './useSceneState'

export function useAppSceneState(opts: {
  eq: Ref<string>
  scene: Ref<SceneId>
  layoutMode: Ref<LayoutMode>
  isTestMode: () => boolean
  isEqAllowed: (eq: string) => boolean
  effectiveEq: ComputedRef<string>
  state: SimulatorAppState
  loadSnapshot: (eq: string) => Promise<{ snapshot: import('../types').GraphSnapshot; sourcePath: string }>
  clearScheduledTimeouts: () => void
  resetCamera: () => void
  resetLayoutKeyCache: () => void
  resetOverlays: () => void
  resizeAndLayout: () => void
  ensureRenderLoop: () => void
  onIncrementalSnapshotLoaded?: (snapshot: import('../types').GraphSnapshot) => void
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
    clearScheduledTimeouts: opts.clearScheduledTimeouts,
    resetCamera: opts.resetCamera,
    resetLayoutKeyCache: opts.resetLayoutKeyCache,
    resetOverlays: opts.resetOverlays,
    resizeAndLayout: opts.resizeAndLayout,
    ensureRenderLoop: opts.ensureRenderLoop,
    onIncrementalSnapshotLoaded: opts.onIncrementalSnapshotLoaded,
    setupResizeListener: opts.setupResizeListener,
    teardownResizeListener: opts.teardownResizeListener,
    stopRenderLoop: opts.stopRenderLoop,
  })
}
