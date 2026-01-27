import type { ComputedRef, Ref } from 'vue'

import type { DemoEvent, GraphSnapshot } from '../types'
import type { LayoutMode } from '../layout/forceLayout'
import type { SceneId } from '../scenes'

import { useAppDemoControls } from './useAppDemoControls'
import { useAppSceneState } from './useAppSceneState'
import { useGeoSimDevHookSetup } from './useGeoSimDevHookSetup'

type AppState = {
  loading: boolean
  error: string
  sourcePath: string
  eventsPath: string
  snapshot: GraphSnapshot | null
  demoTxEvents: import('../types').TxUpdatedEvent[]
  demoClearingPlan: import('../types').ClearingPlanEvent | null
  demoClearingDone: import('../types').ClearingDoneEvent | null
  selectedNodeId: string | null
  flash: number
}

type LoadEventsKind = 'demo-tx' | 'demo-clearing'

type DemoPlayerLike = Parameters<typeof useAppDemoControls>[0]['demoPlayer']

type SceneStateLike = ReturnType<typeof useAppSceneState>

export function useAppSceneAndDemo(opts: {
  // core
  eq: Ref<string>
  scene: Ref<SceneId>
  layoutMode: Ref<LayoutMode>
  effectiveEq: ComputedRef<string>
  state: AppState

  // flags
  isTestMode: () => boolean
  isDev: () => boolean
  isWebDriver: () => boolean
  isEqAllowed: (eq: string) => boolean

  // io
  loadSnapshot: typeof import('../fixtures').loadSnapshot
  loadEvents: typeof import('../fixtures').loadEvents
  assertPlaylistEdgesExistInSnapshot: (opts: { snapshot: GraphSnapshot; events: DemoEvent[]; eventsPath: string }) => void

  // lifecycle hooks
  clearScheduledTimeouts: () => void
  resetOverlays: () => void
  resetCamera: () => void
  resetLayoutKeyCache: () => void
  resizeAndLayout: () => void
  ensureRenderLoop: () => void
  setupResizeListener: () => void
  teardownResizeListener: () => void
  stopRenderLoop: () => void

  // demo
  demoPlayer: DemoPlayerLike
  setSelectedNodeId: (id: string | null) => void

  // dev
  fxState: unknown
}) {
  const demoControls = useAppDemoControls({
    scene: opts.scene,
    isDev: opts.isDev,
    getSnapshot: () => opts.state.snapshot,
    getEffectiveEq: () => opts.effectiveEq.value,
    getDemoTxEvents: () => opts.state.demoTxEvents,
    getDemoClearingPlan: () => opts.state.demoClearingPlan,
    getDemoClearingDone: () => opts.state.demoClearingDone,
    setError: (msg) => {
      opts.state.error = msg
    },
    setSelectedNodeId: opts.setSelectedNodeId,
    demoPlayer: opts.demoPlayer,
    ensureRenderLoop: opts.ensureRenderLoop,
    clearScheduledTimeouts: opts.clearScheduledTimeouts,
    resetOverlays: opts.resetOverlays,
    loadEvents: opts.loadEvents,
    assertPlaylistEdgesExistInSnapshot: opts.assertPlaylistEdgesExistInSnapshot,
  })

  const sceneState: SceneStateLike = useAppSceneState({
    eq: opts.eq,
    scene: opts.scene,
    layoutMode: opts.layoutMode,
    isTestMode: opts.isTestMode,
    isEqAllowed: opts.isEqAllowed,
    effectiveEq: opts.effectiveEq,
    state: opts.state,
    loadSnapshot: opts.loadSnapshot,
    loadEvents: (eq, kind: LoadEventsKind) => opts.loadEvents(eq, kind),
    assertPlaylistEdgesExistInSnapshot: opts.assertPlaylistEdgesExistInSnapshot,
    clearScheduledTimeouts: opts.clearScheduledTimeouts,
    resetPlaylistPointers: demoControls.resetPlaylistPointers,
    resetCamera: opts.resetCamera,
    resetLayoutKeyCache: opts.resetLayoutKeyCache,
    resetOverlays: opts.resetOverlays,
    resizeAndLayout: opts.resizeAndLayout,
    ensureRenderLoop: opts.ensureRenderLoop,
    setupResizeListener: opts.setupResizeListener,
    teardownResizeListener: opts.teardownResizeListener,
    stopRenderLoop: opts.stopRenderLoop,
  })

  const devHook = useGeoSimDevHookSetup({
    isDev: opts.isDev,
    isTestMode: opts.isTestMode,
    isWebDriver: opts.isWebDriver,
    getState: () => opts.state,
    fxState: opts.fxState,
    runTxOnce: demoControls.runTxOnce,
    runClearingOnce: demoControls.runClearingOnce,
  })

  return {
    sceneState,
    setupDevHook: devHook.setupDevHook,

    // demo
    runTxOnce: demoControls.runTxOnce,
    runClearingOnce: demoControls.runClearingOnce,
    canDemoPlay: demoControls.canDemoPlay,
    demoPlayLabel: demoControls.demoPlayLabel,
    demoStepOnce: demoControls.demoStepOnce,
    demoTogglePlay: demoControls.demoTogglePlay,
    demoReset: demoControls.demoReset,
    resetPlaylistPointers: demoControls.resetPlaylistPointers,
  }
}
