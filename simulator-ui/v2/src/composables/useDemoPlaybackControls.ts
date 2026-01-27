import { computed, type ComputedRef } from 'vue'
import type { SceneId } from '../scenes'
import type { ClearingDoneEvent, ClearingPlanEvent, TxUpdatedEvent } from '../types'

type UseDemoPlaybackControlsDeps = {
  getSnapshotReady: () => boolean
  getScene: () => SceneId
  getDemoTxEvents: () => TxUpdatedEvent[]
  getDemoClearingPlan: () => ClearingPlanEvent | null
  getDemoClearingDone: () => ClearingDoneEvent | null

  getPlaylistPlaying: () => boolean

  demoPlayer: {
    runClearingStep: (
      stepIndex: number,
      plan: ClearingPlanEvent,
      done: ClearingDoneEvent | null,
      opts?: { onFinished?: () => void },
    ) => void
    demoStepOnce: (
      scene: SceneId,
      txEvents: TxUpdatedEvent[],
      plan: ClearingPlanEvent | null,
      done: ClearingDoneEvent | null,
    ) => void
    demoTogglePlay: (
      scene: SceneId,
      txEvents: TxUpdatedEvent[],
      plan: ClearingPlanEvent | null,
      done: ClearingDoneEvent | null,
    ) => void
  }

  resetDemoState: () => void
}

type UseDemoPlaybackControlsReturn = {
  canDemoPlay: ComputedRef<boolean>
  demoPlayLabel: ComputedRef<string>
  runClearingStep: (stepIndex: number, opts?: { onFinished?: () => void }) => void
  demoStepOnce: () => void
  demoTogglePlay: () => void
  demoReset: () => void
}

export function useDemoPlaybackControls(deps: UseDemoPlaybackControlsDeps): UseDemoPlaybackControlsReturn {
  const canDemoPlay = computed(() => {
    const scene = deps.getScene()
    if (scene === 'D') return deps.getDemoTxEvents().length > 0
    if (scene === 'E') return !!deps.getDemoClearingPlan()
    return false
  })

  const demoPlayLabel = computed<string>(() => (deps.getPlaylistPlaying() ? 'Pause' : 'Play'))

  function runClearingStep(stepIndex: number, opts?: { onFinished?: () => void }) {
    if (!deps.getSnapshotReady()) return
    const plan = deps.getDemoClearingPlan()
    if (!plan) return
    deps.demoPlayer.runClearingStep(stepIndex, plan, deps.getDemoClearingDone(), opts)
  }

  function demoStepOnce() {
    if (!deps.getSnapshotReady()) return
    if (!canDemoPlay.value) return
    deps.demoPlayer.demoStepOnce(
      deps.getScene(),
      deps.getDemoTxEvents(),
      deps.getDemoClearingPlan(),
      deps.getDemoClearingDone(),
    )
  }

  function demoTogglePlay() {
    if (!deps.getSnapshotReady()) return
    if (!canDemoPlay.value) return
    deps.demoPlayer.demoTogglePlay(
      deps.getScene(),
      deps.getDemoTxEvents(),
      deps.getDemoClearingPlan(),
      deps.getDemoClearingDone(),
    )
  }

  function demoReset() {
    deps.resetDemoState()
  }

  return { canDemoPlay, demoPlayLabel, runClearingStep, demoStepOnce, demoTogglePlay, demoReset }
}
