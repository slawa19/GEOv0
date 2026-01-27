import { describe, expect, it, vi } from 'vitest'
import type { ClearingPlanEvent, TxUpdatedEvent } from '../types'
import { useDemoPlaybackControls } from './useDemoPlaybackControls'

describe('useDemoPlaybackControls', () => {
  it('computes canDemoPlay based on scene + data', () => {
    const deps = {
      getSnapshotReady: () => true,
      getScene: () => 'D' as const,
      getDemoTxEvents: () => [{ event_id: 'e', ts: 't', type: 'tx.updated', equivalent: 'UAH', ttl_ms: 1, edges: [] }] as TxUpdatedEvent[],
      getDemoClearingPlan: () => null,
      getDemoClearingDone: () => null,
      getPlaylistPlaying: () => false,
      demoPlayer: {
        runClearingStep: () => undefined,
        demoStepOnce: () => undefined,
        demoTogglePlay: () => undefined,
      },
      resetDemoState: () => undefined,
    }

    const controls = useDemoPlaybackControls(deps)
    expect(controls.canDemoPlay.value).toBe(true)

    const deps2 = { ...deps, getScene: () => 'E' as const, getDemoTxEvents: () => [] as TxUpdatedEvent[], getDemoClearingPlan: () => null }
    const controls2 = useDemoPlaybackControls(deps2)
    expect(controls2.canDemoPlay.value).toBe(false)

    const plan: ClearingPlanEvent = { event_id: 'p', ts: 't', type: 'clearing.plan', equivalent: 'UAH', plan_id: 'p1', steps: [] }
    const deps3 = { ...deps2, getDemoClearingPlan: () => plan }
    const controls3 = useDemoPlaybackControls(deps3)
    expect(controls3.canDemoPlay.value).toBe(true)
  })

  it('guards demoStepOnce and demoTogglePlay when snapshot missing or canDemoPlay=false', () => {
    const demoStepOnce = vi.fn()
    const demoTogglePlay = vi.fn()

    const deps = {
      getSnapshotReady: () => false,
      getScene: () => 'D' as const,
      getDemoTxEvents: () => [] as TxUpdatedEvent[],
      getDemoClearingPlan: () => null,
      getDemoClearingDone: () => null,
      getPlaylistPlaying: () => false,
      demoPlayer: {
        runClearingStep: () => undefined,
        demoStepOnce,
        demoTogglePlay,
      },
      resetDemoState: () => undefined,
    }

    const controls = useDemoPlaybackControls(deps)
    controls.demoStepOnce()
    controls.demoTogglePlay()

    expect(demoStepOnce).toHaveBeenCalledTimes(0)
    expect(demoTogglePlay).toHaveBeenCalledTimes(0)
  })

  it('demoPlayLabel reflects playlist.playing', () => {
    const deps = {
      getSnapshotReady: () => true,
      getScene: () => 'A' as const,
      getDemoTxEvents: () => [] as TxUpdatedEvent[],
      getDemoClearingPlan: () => null,
      getDemoClearingDone: () => null,
      getPlaylistPlaying: () => true,
      demoPlayer: {
        runClearingStep: () => undefined,
        demoStepOnce: () => undefined,
        demoTogglePlay: () => undefined,
      },
      resetDemoState: () => undefined,
    }

    const controls = useDemoPlaybackControls(deps)
    expect(controls.demoPlayLabel.value).toBe('Pause')
  })
})
