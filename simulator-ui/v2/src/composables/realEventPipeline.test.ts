import { describe, expect, it, vi } from 'vitest'

import type { AcceptedSimulatorEvent } from '../api/normalizeSimulatorEvent'
import type { GraphSnapshot } from '../types'
import type { SimulatorAppState } from '../types/simulatorApp'
import {
  applyAcceptedRealEvent,
  createRealEventReplayOwner,
  executeRealEventIntents,
  type RealEventDraft,
  type RealEventStateDeps,
} from './realEventPipeline'

const TS = '2026-01-01T00:00:00Z'

function createDraft(snapshot: GraphSnapshot | null = null): RealEventDraft {
  return {
    real: {
      runId: 'run_1',
      runStatus: null,
      lastError: '',
      runStats: {
        attempts: 0,
        committed: 0,
        rejected: 0,
        errors: 0,
        timeouts: 0,
        rejectedByCode: {},
        errorsByCode: {},
      },
    },
    state: {
      loading: false,
      error: '',
      sourcePath: '',
      snapshot,
      selectedNodeId: null,
      flash: 0,
    } satisfies SimulatorAppState,
  }
}

function createStateDeps(trace: string[], draft: RealEventDraft): RealEventStateDeps {
  return {
    patchApplier: {
      applyNodePatches: (patches) => {
        if (!patches?.length) return
        const node = draft.state.snapshot?.nodes.find((item) => item.id === patches[0]!.id)
        if (node && patches[0]!.net_balance !== undefined) node.net_balance = patches[0]!.net_balance
        trace.push('state:node-patch')
      },
      applyEdgePatches: (patches) => {
        if (!patches?.length) return
        trace.push('state:edge-patch')
      },
    },
    isUserFacingRunError: (code) => code === 'INTERNAL_ERROR',
    inc: (map, key) => {
      map[key] = (map[key] ?? 0) + 1
    },
  }
}

function execute(intents: ReturnType<typeof applyAcceptedRealEvent>, draft: RealEventDraft, trace: string[]) {
  executeRealEventIntents(intents, {
    getActiveRunId: () => draft.real.runId,
    onAnySseEvent: () => trace.push(`effect:any:${draft.real.runStats.attempts}`),
    refreshSnapshot: () => trace.push('effect:refresh'),
    wakeUp: () => trace.push('effect:wake'),
    runRealTxFx: () => trace.push(`effect:tx-fx:${draft.real.runStats.committed}`),
    runRealClearingDoneFx: () => trace.push(`effect:clearing-fx:${draft.state.snapshot?.nodes[0]?.net_balance}`),
    pushTxAmountLabel: (_nodeId, amount) => trace.push(`effect:label:${amount}`),
    clampRealTxTtlMs: (ttl) => Number(ttl),
    scheduleTimeout: (fn) => {
      trace.push('effect:schedule')
      fn()
    },
  })
}

describe('realEventPipeline replay admission', () => {
  const tx = (eventId: string): AcceptedSimulatorEvent => ({
    event_id: eventId,
    ts: TS,
    type: 'tx.updated',
    equivalent: 'EUR',
    from: 'A',
    to: 'B',
    amount: '1',
    ttl_ms: 100,
    edges: [{ from: 'A', to: 'B' }],
  })

  it('accepts an unseen lower producer sequence without regressing the reconnect cursor', () => {
    const owner = createRealEventReplayOwner()
    const input = (event: AcceptedSimulatorEvent, currentCursor: string | null) => ({
      event,
      frameId: event.event_id,
      connectionRunId: 'run_1',
      activeRunId: 'run_1',
      connectionEquivalent: 'EUR',
      currentCursor,
    })

    expect(owner.admit(input(tx('evt_run_1_20'), null))).toEqual({ status: 'accepted', cursor: 'evt_run_1_20' })
    owner.commit('evt_run_1_20', 'run_1')
    expect(owner.admit(input(tx('evt_run_1_10'), 'evt_run_1_20'))).toEqual({
      status: 'accepted',
      cursor: 'evt_run_1_20',
    })
  })

  it('applies ordered replay before authoritative status without double-counting or cursor skip', () => {
    const owner = createRealEventReplayOwner()
    const draft = createDraft()
    const trace: string[] = []
    let cursor: string | null = 'evt_run_1_9'
    const events: AcceptedSimulatorEvent[] = [
      tx('evt_run_1_10'),
      {
        event_id: 'evt_run_1_11',
        ts: TS,
        type: 'run_status',
        run_id: 'run_1',
        scenario_id: 'scenario_1',
        state: 'running',
        attempts_total: 1,
        committed_total: 1,
        rejected_total: 0,
        errors_total: 0,
        timeouts_total: 0,
        last_error: null,
      },
      tx('evt_run_1_10'),
    ]

    for (const event of events) {
      const admission = owner.admit({
        event,
        frameId: event.event_id,
        connectionRunId: 'run_1',
        activeRunId: 'run_1',
        connectionEquivalent: 'EUR',
        currentCursor: cursor,
      })
      if (admission.status !== 'accepted') continue
      applyAcceptedRealEvent(event, 'run_1', draft, createStateDeps(trace, draft))
      owner.commit(event.event_id, 'run_1')
      cursor = admission.cursor
    }

    expect(draft.real.runStats).toMatchObject({ attempts: 1, committed: 1 })
    expect(cursor).toBe('evt_run_1_11')
  })

  it('does not suppress an accepted event until trusted state application commits it', () => {
    const owner = createRealEventReplayOwner()
    const event = tx('evt_run_1_30')
    const input = {
      event,
      frameId: event.event_id,
      connectionRunId: 'run_1',
      activeRunId: 'run_1',
      connectionEquivalent: 'EUR',
      currentCursor: null,
    }

    expect(owner.admit(input)).toMatchObject({ status: 'accepted' })
    expect(owner.admit(input)).toMatchObject({ status: 'accepted' })
    owner.commit(event.event_id, 'run_1')
    expect(owner.admit(input)).toMatchObject({ status: 'duplicate' })
  })

  it('rejects frame and connection context mismatches before entering dedup', () => {
    const owner = createRealEventReplayOwner()
    const event = tx('evt_run_1_1')
    expect(
      owner.admit({
        event,
        frameId: 'other',
        connectionRunId: 'run_1',
        activeRunId: 'run_1',
        connectionEquivalent: 'EUR',
        currentCursor: null,
      }),
    ).toMatchObject({ status: 'rejected', diagnostic: 'frame_id_mismatch' })
    expect(
      owner.admit({
        event,
        frameId: event.event_id,
        connectionRunId: 'run_1',
        activeRunId: 'run_2',
        connectionEquivalent: 'EUR',
        currentCursor: null,
      }),
    ).toMatchObject({ status: 'rejected', diagnostic: 'active_run_changed' })
    expect(
      owner.admit({
        event,
        frameId: event.event_id,
        connectionRunId: 'run_1',
        activeRunId: 'run_1',
        connectionEquivalent: 'EUR',
        currentCursor: null,
      }),
    ).toEqual({ status: 'accepted', cursor: event.event_id })
  })
})

describe('realEventPipeline state-before-effect ordering', () => {
  it('lifecycle mutates status and totals before accepted-event notification', () => {
    const trace: string[] = []
    const draft = createDraft()
    const event: AcceptedSimulatorEvent = {
      event_id: 'evt_run_1_1',
      ts: TS,
      type: 'run_status',
      run_id: 'run_1',
      scenario_id: 'scenario_1',
      state: 'running',
      attempts_total: 7,
      last_error: null,
    }
    const intents = applyAcceptedRealEvent(event, 'run_1', draft, createStateDeps(trace, draft))
    trace.push(`state:status:${draft.real.runStatus?.state}:${draft.real.runStats.attempts}`)
    execute(intents, draft, trace)
    expect(trace).toEqual(['state:status:running:7', 'effect:any:7'])
  })

  it('topology applies patches before accepted notification and wake', () => {
    const trace: string[] = []
    const draft = createDraft({ equivalent: 'EUR', generated_at: TS, nodes: [{ id: 'A' }], links: [] })
    const event: AcceptedSimulatorEvent = {
      event_id: 'evt_run_1_2',
      ts: '2026-01-01T00:00:01Z',
      type: 'topology.changed',
      equivalent: 'EUR',
      payload: {
        added_nodes: [],
        removed_nodes: [],
        added_edges: [],
        removed_edges: [],
        node_patch: [{ id: 'A', net_balance: '5' }],
      },
    }
    const intents = applyAcceptedRealEvent(event, 'run_1', draft, createStateDeps(trace, draft))
    trace.push(`state:topology:${draft.state.snapshot?.nodes[0]?.net_balance}`)
    execute(intents, draft, trace)
    expect(trace).toEqual(['state:node-patch', 'state:topology:5', 'effect:any:0', 'effect:wake'])
  })

  it('topology structural changes commit locally and leave relayout to the snapshot structural watcher', () => {
    const trace: string[] = []
    const draft = createDraft({ equivalent: 'EUR', generated_at: TS, nodes: [{ id: 'A' }], links: [] })
    const event: AcceptedSimulatorEvent = {
      event_id: 'evt_run_1_structural',
      ts: '2026-01-01T00:00:01Z',
      type: 'topology.changed',
      equivalent: 'EUR',
      payload: {
        added_nodes: [{ pid: 'B', name: 'Node B', type: 'person' }],
        removed_nodes: [],
        added_edges: [{ from_pid: 'A', to_pid: 'B', equivalent_code: 'EUR', limit: '10' }],
        removed_edges: [],
      },
    }

    const intents = applyAcceptedRealEvent(event, 'run_1', draft, createStateDeps(trace, draft))
    trace.push(`state:topology:${draft.state.snapshot?.nodes.length}:${draft.state.snapshot?.links.length}`)
    execute(intents, draft, trace)

    expect(trace).toEqual(['state:topology:2:1', 'effect:any:0', 'effect:wake'])
  })

  it('payment applies counters and graph patches before FX, wake and labels', () => {
    const trace: string[] = []
    const draft = createDraft({ equivalent: 'EUR', generated_at: TS, nodes: [{ id: 'A' }, { id: 'B' }], links: [] })
    const event: AcceptedSimulatorEvent = {
      event_id: 'evt_run_1_3',
      ts: TS,
      type: 'tx.updated',
      equivalent: 'EUR',
      from: 'A',
      to: 'B',
      amount: '3',
      ttl_ms: 10,
      edges: [{ from: 'A', to: 'B' }],
      node_patch: [{ id: 'A', net_balance: '-3' }],
    }
    const intents = applyAcceptedRealEvent(event, 'run_1', draft, createStateDeps(trace, draft))
    trace.push(`state:payment:${draft.real.runStats.committed}:${draft.state.snapshot?.nodes[0]?.net_balance}`)
    execute(intents, draft, trace)
    expect(trace).toEqual([
      'state:node-patch',
      'state:payment:1:-3',
      'effect:any:1',
      'effect:tx-fx:1',
      'effect:wake',
      'effect:label:-3',
      'effect:schedule',
      'effect:label:+3',
    ])
  })

  it('clearing applies graph patches before completion FX and wake', () => {
    const trace: string[] = []
    const draft = createDraft({ equivalent: 'EUR', generated_at: TS, nodes: [{ id: 'A' }], links: [] })
    const event: AcceptedSimulatorEvent = {
      event_id: 'evt_run_1_4',
      ts: TS,
      type: 'clearing.done',
      equivalent: 'EUR',
      plan_id: 'plan_1',
      cycle_edges: [{ from: 'A', to: 'B' }],
      node_patch: [{ id: 'A', net_balance: '0' }],
    }
    const intents = applyAcceptedRealEvent(event, 'run_1', draft, createStateDeps(trace, draft))
    trace.push(`state:clearing:${draft.state.snapshot?.nodes[0]?.net_balance}`)
    execute(intents, draft, trace)
    expect(trace).toEqual([
      'state:node-patch',
      'state:clearing:0',
      'effect:any:0',
      'effect:clearing-fx:0',
      'effect:wake',
    ])
  })

  it('tx.failed updates rejection state but emits no optional FX intent', () => {
    const trace: string[] = []
    const draft = createDraft()
    const event: AcceptedSimulatorEvent = {
      event_id: 'evt_run_1_5',
      ts: TS,
      type: 'tx.failed',
      equivalent: 'EUR',
      error: { code: 'NO_CAPACITY', message: 'no route', at: TS },
    }
    const intents = applyAcceptedRealEvent(event, 'run_1', draft, createStateDeps(trace, draft))
    const txFx = vi.fn()
    executeRealEventIntents(intents, {
      getActiveRunId: () => draft.real.runId,
      refreshSnapshot: vi.fn(),
      runRealTxFx: txFx,
      runRealClearingDoneFx: vi.fn(),
      pushTxAmountLabel: vi.fn(),
      clampRealTxTtlMs: () => 10,
      scheduleTimeout: vi.fn(),
    })
    expect(draft.real.runStats).toMatchObject({ attempts: 1, rejected: 1, errors: 0, timeouts: 0 })
    expect(txFx).not.toHaveBeenCalled()
    expect(intents).toEqual([{ type: 'accepted-event' }])
  })

  it('topology requests a refresh when no trusted snapshot exists', () => {
    const trace: string[] = []
    const draft = createDraft(null)
    const event: AcceptedSimulatorEvent = {
      event_id: 'evt_run_1_6',
      ts: TS,
      type: 'topology.changed',
      equivalent: 'EUR',
      payload: { added_nodes: [], removed_nodes: [], added_edges: [], removed_edges: [] },
    }
    const intents = applyAcceptedRealEvent(event, 'run_1', draft, createStateDeps(trace, draft))
    expect(intents).toEqual([{ type: 'accepted-event' }, { type: 'refresh-snapshot' }])
  })

  it('optional FX policy suppresses animation while preserving labels, clearing information, notification and wake', () => {
    const trace: string[] = []
    const clearing = {
      event_id: 'evt_run_1_8',
      ts: TS,
      type: 'clearing.done' as const,
      equivalent: 'EUR',
      plan_id: 'plan_1',
      cycle_edges: [{ from: 'A', to: 'B' }],
    }
    executeRealEventIntents(
      [
        { type: 'accepted-event' },
        {
          type: 'tx-fx',
          event: {
            event_id: 'evt_run_1_7',
            ts: TS,
            type: 'tx.updated',
            equivalent: 'EUR',
            ttl_ms: 1200,
            edges: [{ from: 'A', to: 'B' }],
          },
        },
        { type: 'tx-label', role: 'sender', nodeId: 'A', signedAmount: '-1', unit: 'EUR', throttleMs: 0 },
        {
          type: 'tx-label-delayed',
          role: 'receiver',
          nodeId: 'B',
          signedAmount: '+1',
          unit: 'EUR',
          throttleMs: 0,
          delayMs: 10,
          runId: 'run_1',
        },
        { type: 'clearing-fx', event: clearing },
        { type: 'wake' },
      ],
      {
        getActiveRunId: () => 'run_1',
        optionalFxEnabled: () => false,
        onAnySseEvent: () => trace.push('any'),
        refreshSnapshot: vi.fn(),
        wakeUp: () => trace.push('wake'),
        runRealTxFx: () => trace.push('fx'),
        runRealClearingDoneFx: () => trace.push('clearing'),
        pushTxAmountLabel: (_nodeId, amount) => trace.push(`label:${amount}`),
        clampRealTxTtlMs: () => 10,
        scheduleTimeout: (fn) => fn(),
      },
    )
    expect(trace).toEqual(['any', 'label:-1', 'label:+1', 'clearing', 'wake'])
  })

  it('reports an explicit amount flyout that lacks label data through an injectable contract warning', () => {
    const trace: string[] = []
    const draft = createDraft()
    const event: AcceptedSimulatorEvent = {
      event_id: 'evt_run_1_warning',
      ts: TS,
      type: 'tx.updated',
      equivalent: 'EUR',
      amount_flyout: true,
      ttl_ms: 100,
      edges: [],
    }
    const intents = applyAcceptedRealEvent(event, 'run_1', draft, createStateDeps(trace, draft))
    const warnContract = vi.fn()

    executeRealEventIntents(intents, {
      getActiveRunId: () => 'run_1',
      refreshSnapshot: vi.fn(),
      runRealTxFx: vi.fn(),
      runRealClearingDoneFx: vi.fn(),
      pushTxAmountLabel: vi.fn(),
      clampRealTxTtlMs: () => 10,
      scheduleTimeout: vi.fn(),
      warnContract,
    })

    expect(warnContract).toHaveBeenCalledWith(
      'tx.updated amount_flyout contract violated (missing amount/endpoints)',
      { event_id: event.event_id, amount: '', from: undefined, to: undefined, edges_len: 0 },
    )
  })
})
