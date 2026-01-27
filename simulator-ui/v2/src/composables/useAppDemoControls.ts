import type { Ref } from 'vue'
import type { ClearingDoneEvent, ClearingPlanEvent, DemoEvent, GraphSnapshot, TxUpdatedEvent } from '../types'
import type { SceneId } from '../scenes'
import { useDemoActions } from './useDemoActions'
import { useDemoPlaybackControls } from './useDemoPlaybackControls'

type DemoPlayerLike = {
  playlist: { playing: boolean }
  stopPlaylistPlayback: () => void
  resetPlaylistPointers: () => void
  resetDemoState: () => void
  runTxEvent: (evt: TxUpdatedEvent, opts?: { onFinished?: () => void }) => void
  runClearingOnce: (plan: ClearingPlanEvent, done: ClearingDoneEvent | null) => void
  runClearingStep: (
    stepIndex: number,
    plan: ClearingPlanEvent,
    done: ClearingDoneEvent | null,
    opts?: { onFinished?: () => void },
  ) => void
  demoStepOnce: (sceneId: SceneId, tx: TxUpdatedEvent[], plan: ClearingPlanEvent | null, done: ClearingDoneEvent | null) => void
  demoTogglePlay: (sceneId: SceneId, tx: TxUpdatedEvent[], plan: ClearingPlanEvent | null, done: ClearingDoneEvent | null) => void
}

export function useAppDemoControls(opts: {
  scene: Ref<SceneId>
  isDev: () => boolean
  getSnapshot: () => GraphSnapshot | null
  getEffectiveEq: () => string
  getDemoTxEvents: () => TxUpdatedEvent[]
  getDemoClearingPlan: () => ClearingPlanEvent | null
  getDemoClearingDone: () => ClearingDoneEvent | null
  setError: (msg: string) => void
  setSelectedNodeId: (id: string | null) => void
  demoPlayer: DemoPlayerLike
  ensureRenderLoop: () => void
  clearScheduledTimeouts: () => void
  resetOverlays: () => void
  loadEvents: typeof import('../fixtures').loadEvents
  assertPlaylistEdgesExistInSnapshot: (opts: { snapshot: GraphSnapshot; events: DemoEvent[]; eventsPath: string }) => void
}) {
  function stopPlaylistPlayback() {
    opts.demoPlayer.stopPlaylistPlayback()
  }

  function resetPlaylistPointers() {
    opts.demoPlayer.resetPlaylistPointers()
  }

  function resetDemoState() {
    opts.demoPlayer.resetDemoState()
    opts.setSelectedNodeId(null)
  }

  const demoActions = useDemoActions({
    getSnapshot: opts.getSnapshot,
    getEffectiveEq: opts.getEffectiveEq,
    getDemoTxEvents: opts.getDemoTxEvents,
    getDemoClearingPlan: opts.getDemoClearingPlan,
    getDemoClearingDone: opts.getDemoClearingDone,
    setError: opts.setError,
    stopPlaylistPlayback,
    ensureRenderLoop: opts.ensureRenderLoop,
    clearScheduledTimeouts: opts.clearScheduledTimeouts,
    resetOverlays: opts.resetOverlays,
    loadEvents: opts.loadEvents,
    assertPlaylistEdgesExistInSnapshot: opts.assertPlaylistEdgesExistInSnapshot,
    runTxEvent: (evt) => {
      opts.demoPlayer.runTxEvent(evt)
    },
    runClearingOnce: (plan, done) => {
      opts.demoPlayer.runClearingOnce(plan, done)
    },
    dev: {
      isDev: opts.isDev,
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

  const demoPlayback = useDemoPlaybackControls({
    getSnapshotReady: () => !!opts.getSnapshot(),
    getScene: () => opts.scene.value,
    getDemoTxEvents: opts.getDemoTxEvents,
    getDemoClearingPlan: opts.getDemoClearingPlan,
    getDemoClearingDone: opts.getDemoClearingDone,
    getPlaylistPlaying: () => opts.demoPlayer.playlist.playing,
    demoPlayer: {
      runClearingStep: (stepIndex, plan, done, o) => opts.demoPlayer.runClearingStep(stepIndex, plan, done, o),
      demoStepOnce: (s, tx, plan, done) => opts.demoPlayer.demoStepOnce(s, tx, plan, done),
      demoTogglePlay: (s, tx, plan, done) => opts.demoPlayer.demoTogglePlay(s, tx, plan, done),
    },
    resetDemoState,
  })

  return {
    // demo actions
    runTxOnce: demoActions.runTxOnce,
    runClearingOnce: demoActions.runClearingOnce,

    // playback
    canDemoPlay: demoPlayback.canDemoPlay,
    demoPlayLabel: demoPlayback.demoPlayLabel,
    runClearingStep: demoPlayback.runClearingStep,
    demoStepOnce: demoPlayback.demoStepOnce,
    demoTogglePlay: demoPlayback.demoTogglePlay,
    demoReset: demoPlayback.demoReset,

    // misc (scene state needs this)
    resetPlaylistPointers,
  }
}
