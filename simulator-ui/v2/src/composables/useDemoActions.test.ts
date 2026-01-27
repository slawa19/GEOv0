import { describe, expect, it, vi } from 'vitest'
import type { ClearingDoneEvent, ClearingPlanEvent, DemoEvent, GraphSnapshot, TxUpdatedEvent } from '../types'
import { useDemoActions } from './useDemoActions'

function makeSnapshot(): GraphSnapshot {
  return {
    equivalent: 'UAH',
    generated_at: 't',
    nodes: [{ id: 'A' }, { id: 'B' }],
    links: [{ source: 'A', target: 'B' }],
  }
}

describe('useDemoActions', () => {
  it('runTxOnce loads events when no demoTxEvents and runs tx event', async () => {
    const snapshot = makeSnapshot()

    const tx: TxUpdatedEvent = {
      event_id: '1',
      ts: 't',
      type: 'tx.updated',
      equivalent: 'UAH',
      ttl_ms: 1,
      edges: [{ from: 'A', to: 'B' }],
    }

    const runTxEvent = vi.fn()
    const assertEdges = vi.fn()

    const a = useDemoActions({
      getSnapshot: () => snapshot,
      getEffectiveEq: () => 'UAH',
      getDemoTxEvents: () => [],
      getDemoClearingPlan: () => null,
      getDemoClearingDone: () => null,
      setError: vi.fn(),
      stopPlaylistPlayback: vi.fn(),
      ensureRenderLoop: vi.fn(),
      clearScheduledTimeouts: vi.fn(),
      resetOverlays: vi.fn(),
      loadEvents: vi.fn(async () => ({ events: [tx] as DemoEvent[], sourcePath: 'events.json' })),
      assertPlaylistEdgesExistInSnapshot: assertEdges,
      runTxEvent,
      runClearingOnce: vi.fn(),
      dev: { isDev: () => false },
    })

    await a.runTxOnce()

    expect(assertEdges).toHaveBeenCalledTimes(1)
    expect(runTxEvent).toHaveBeenCalledWith(tx)
  })

  it('runClearingOnce uses existing plan if present', async () => {
    const snapshot = makeSnapshot()

    const plan: ClearingPlanEvent = {
      event_id: 'p',
      ts: 't',
      type: 'clearing.plan',
      equivalent: 'UAH',
      plan_id: 'p',
      steps: [],
    }

    const done: ClearingDoneEvent = {
      event_id: 'd',
      ts: 't',
      type: 'clearing.done',
      equivalent: 'UAH',
      plan_id: 'p',
    }

    const runClearingOnce = vi.fn()

    const a = useDemoActions({
      getSnapshot: () => snapshot,
      getEffectiveEq: () => 'UAH',
      getDemoTxEvents: () => [],
      getDemoClearingPlan: () => plan,
      getDemoClearingDone: () => done,
      setError: vi.fn(),
      stopPlaylistPlayback: vi.fn(),
      ensureRenderLoop: vi.fn(),
      clearScheduledTimeouts: vi.fn(),
      resetOverlays: vi.fn(),
      loadEvents: vi.fn(async () => ({ events: [] as DemoEvent[], sourcePath: 'events.json' })),
      assertPlaylistEdgesExistInSnapshot: vi.fn(),
      runTxEvent: vi.fn(),
      runClearingOnce,
      dev: { isDev: () => false },
    })

    await a.runClearingOnce()

    expect(runClearingOnce).toHaveBeenCalledWith(plan, done)
  })

  it('sets error and calls dev hooks on failures', async () => {
    const snapshot = makeSnapshot()

    const setError = vi.fn()
    const onTxError = vi.fn()

    const a = useDemoActions({
      getSnapshot: () => snapshot,
      getEffectiveEq: () => 'UAH',
      getDemoTxEvents: () => [],
      getDemoClearingPlan: () => null,
      getDemoClearingDone: () => null,
      setError,
      stopPlaylistPlayback: vi.fn(),
      ensureRenderLoop: vi.fn(),
      clearScheduledTimeouts: vi.fn(),
      resetOverlays: vi.fn(),
      loadEvents: vi.fn(async () => {
        throw new Error('boom')
      }),
      assertPlaylistEdgesExistInSnapshot: vi.fn(),
      runTxEvent: vi.fn(),
      runClearingOnce: vi.fn(),
      dev: { isDev: () => true, onTxError },
    })

    await a.runTxOnce()

    expect(setError).toHaveBeenCalledWith('boom')
    expect(onTxError).toHaveBeenCalled()
  })
})
