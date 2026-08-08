import { describe, expect, it } from 'vitest'

import {
  normalizeSimulatorEvent,
  type AcceptedSimulatorEvent,
  type SimulatorEventNormalization,
} from './normalizeSimulatorEvent'

function requireEventType<TType extends AcceptedSimulatorEvent['type']>(
  result: SimulatorEventNormalization,
  type: TType,
): Extract<AcceptedSimulatorEvent, { type: TType }> {
  expect(result.status).toBe('accepted')
  if (result.status !== 'accepted') throw new Error(`expected accepted ${type}`)
  expect(result.event.type).toBe(type)
  return result.event as Extract<AcceptedSimulatorEvent, { type: TType }>
}

function requireIgnored(result: SimulatorEventNormalization, kind: 'malformed' | 'unknown', diagnostic: string) {
  expect(result).toMatchObject({ status: 'ignored', kind, diagnostic })
}

describe('normalizeSimulatorEvent', () => {
  it('normalizes tx.updated edges=null into []', () => {
    const evt = normalizeSimulatorEvent({
      event_id: 'evt_1',
      ts: '2026-01-01T00:00:00Z',
      type: 'tx.updated',
      equivalent: 'UAH',
      ttl_ms: 1200,
      edges: null,
    })

    const txUpdatedEvent = requireEventType(evt, 'tx.updated')
    expect(txUpdatedEvent.edges).toEqual([])
  })
  it('normalizeSimulatorEvent: tx.updated', () => {
    const raw = {
      event_id: 'evt_1',
      ts: '2026-01-01T00:00:00Z',
      type: 'tx.updated',
      equivalent: 'UAH',
      from: 'A',
      to: 'B',
      amount: '123.45',
      amount_flyout: true,
      ttl_ms: 1200,
      intensity_key: 'mid',
      edges: [{ from: 'A', to: 'B' }],
    }

    const evt = requireEventType(normalizeSimulatorEvent(raw), 'tx.updated')
    expect(evt).toEqual({
      event_id: 'evt_1',
      ts: '2026-01-01T00:00:00Z',
      type: 'tx.updated',
      equivalent: 'UAH',
      from: 'A',
      to: 'B',
      amount: '123.45',
      amount_flyout: true,
      ttl_ms: 1200,
      intensity_key: 'mid',
      edges: [{ from: 'A', to: 'B' }],
      node_badges: [],
      node_patch: undefined,
      edge_patch: undefined,
    })
  })

  it('normalizeSimulatorEvent: tx.updated without amount keeps event (amount undefined)', () => {
    const evt = normalizeSimulatorEvent({
      event_id: 'evt_3',
      ts: '2026-01-01T00:00:00Z',
      type: 'tx.updated',
      equivalent: 'UAH',
      from: 'A',
      to: 'B',
      edges: [{ from: 'A', to: 'B' }],
    })

    const txUpdatedEvent = requireEventType(evt, 'tx.updated')
    expect(txUpdatedEvent.amount).toBeUndefined()
    expect(txUpdatedEvent.from).toBe('A')
    expect(txUpdatedEvent.to).toBe('B')
    expect(txUpdatedEvent.edges).toEqual([{ from: 'A', to: 'B' }])
  })

  it('normalizeSimulatorEvent: tx.updated with missing from/to and empty edges keeps event', () => {
    const evt = normalizeSimulatorEvent({
      event_id: 'evt_4',
      ts: '2026-01-01T00:00:00Z',
      type: 'tx.updated',
      equivalent: 'UAH',
      edges: [],
    })

    const txUpdatedEvent = requireEventType(evt, 'tx.updated')
    expect(txUpdatedEvent.from).toBeUndefined()
    expect(txUpdatedEvent.to).toBeUndefined()
    expect(txUpdatedEvent.amount).toBeUndefined()
    expect(txUpdatedEvent.edges).toEqual([])
  })

  it('normalizeSimulatorEvent: clearing.done', () => {
    const raw = {
      event_id: 'evt_2',
      ts: '2026-01-01T00:00:01Z',
      type: 'clearing.done',
      equivalent: 'UAH',
      plan_id: 'plan_x',
      cleared_cycles: 2,
      cleared_amount: '10.00',
      node_patch: [{ id: 'A', net_balance: '1.00', net_balance_atoms: '100', net_sign: 1 }],
      edge_patch: [{ source: 'A', target: 'B', used: '1.00', available: '9.00' }],
    }

    const evt = requireEventType(normalizeSimulatorEvent(raw), 'clearing.done')
    expect(evt).toEqual({
      event_id: 'evt_2',
      ts: '2026-01-01T00:00:01Z',
      type: 'clearing.done',
      equivalent: 'UAH',
      plan_id: 'plan_x',
      cleared_cycles: 2,
      cleared_amount: '10.00',
      node_patch: [{ id: 'A', net_balance: '1.00', net_balance_atoms: '100', net_sign: 1 }],
      edge_patch: [{ source: 'A', target: 'B', used: '1.00', available: '9.00' }],
    })
  })

  it('normalizeSimulatorEvent: run_status parses totals + stall ticks', () => {
    const raw = {
      event_id: 'evt_rs_1',
      ts: '2026-01-01T00:00:02Z',
      type: 'run_status',
      run_id: 'run_1',
      scenario_id: 'sc_1',
      state: 'running',
      sim_time_ms: 1234,
      intensity_percent: 50,
      ops_sec: 12,
      queue_depth: 0,

      attempts_total: 10,
      committed_total: 7,
      rejected_total: 2,
      errors_total: 1,
      timeouts_total: 0,
      consec_all_rejected_ticks: 3,
    }

    const evt = requireEventType(normalizeSimulatorEvent(raw), 'run_status')
    expect(evt).toEqual({
      event_id: 'evt_rs_1',
      ts: '2026-01-01T00:00:02Z',
      type: 'run_status',
      run_id: 'run_1',
      scenario_id: 'sc_1',
      state: 'running',
      sim_time_ms: 1234,
      intensity_percent: 50,
      ops_sec: 12,
      queue_depth: 0,
      last_event_type: null,
      current_phase: null,
      last_error: null,

      attempts_total: 10,
      committed_total: 7,
      rejected_total: 2,
      errors_total: 1,
      timeouts_total: 0,
      consec_all_rejected_ticks: 3,
    })
  })

  it('accepts canonical nullable run_status metrics', () => {
    const evt = requireEventType(
      normalizeSimulatorEvent({
        event_id: 'evt_rs_nullable',
        ts: '2026-01-01T00:00:02Z',
        type: 'run_status',
        run_id: 'run_1',
        scenario_id: 'sc_1',
        state: 'running',
        sim_time_ms: null,
        intensity_percent: null,
        ops_sec: null,
        queue_depth: null,
      }),
      'run_status',
    )

    expect(evt.sim_time_ms).toBeNull()
    expect(evt.intensity_percent).toBeNull()
    expect(evt.ops_sec).toBeNull()
    expect(evt.queue_depth).toBeNull()
  })

  it('accepts tx.failed without optional endpoints', () => {
    const evt = requireEventType(
      normalizeSimulatorEvent({
        event_id: 'evt_failed_1',
        ts: '2026-01-01T00:00:03Z',
        type: 'tx.failed',
        equivalent: 'UAH',
        error: { code: 'PAYMENT_REJECTED', message: 'No route', at: '2026-01-01T00:00:03Z' },
      }),
      'tx.failed',
    )

    expect(evt.from).toBeUndefined()
    expect(evt.to).toBeUndefined()
    expect(evt.error.code).toBe('PAYMENT_REJECTED')
  })

  it('explicitly ignores unknown event types', () => {
    const result = normalizeSimulatorEvent({
      event_id: 'evt_unknown_1',
      ts: '2026-01-01T00:00:04Z',
      type: 'future.event',
    })

    requireIgnored(result, 'unknown', 'unknown_event_type')
    expect(result).toMatchObject({ eventType: 'future.event' })
  })

  it.each([
    {
      label: 'tx.updated edge',
      payload: {
        event_id: 'evt_bad_tx_edge',
        ts: '2026-01-01T00:00:05Z',
        type: 'tx.updated',
        equivalent: 'UAH',
        edges: [{ from: 'A' }],
      },
      diagnostic: 'invalid_tx_updated_collection',
    },
    {
      label: 'tx.updated patch',
      payload: {
        event_id: 'evt_bad_tx_patch',
        ts: '2026-01-01T00:00:05Z',
        type: 'tx.updated',
        equivalent: 'UAH',
        edges: [],
        node_patch: [{ id: 'A' }, { net_balance: '1.00' }],
      },
      diagnostic: 'invalid_tx_updated_collection',
    },
    {
      label: 'tx.failed error',
      payload: {
        event_id: 'evt_bad_failed',
        ts: '2026-01-01T00:00:05Z',
        type: 'tx.failed',
        equivalent: 'UAH',
        error: { code: 42, message: 'bad' },
      },
      diagnostic: 'invalid_tx_failed_error',
    },
    {
      label: 'clearing.done cycle edge',
      payload: {
        event_id: 'evt_bad_clearing',
        ts: '2026-01-01T00:00:05Z',
        type: 'clearing.done',
        equivalent: 'UAH',
        plan_id: 'plan_1',
        cycle_edges: [{ from: 'A' }],
      },
      diagnostic: 'invalid_clearing_done_payload',
    },
    {
      label: 'topology.changed node',
      payload: {
        event_id: 'evt_bad_topology',
        ts: '2026-01-01T00:00:05Z',
        type: 'topology.changed',
        equivalent: 'UAH',
        payload: { added_nodes: [{ name: 'missing pid' }] },
      },
      diagnostic: 'invalid_topology_changed_node',
    },
    {
      label: 'topology.changed patch',
      payload: {
        event_id: 'evt_bad_topology_patch',
        ts: '2026-01-01T00:00:05Z',
        type: 'topology.changed',
        equivalent: 'UAH',
        payload: { edge_patch: [{ source: 'A' }] },
      },
      diagnostic: 'invalid_topology_changed_collection',
    },
  ])('rejects a malformed $label instead of partially applying it', ({ payload, diagnostic }) => {
    requireIgnored(normalizeSimulatorEvent(payload), 'malformed', diagnostic)
  })

  it('rejects tx.updated when amount_flyout=true violates its producer invariant', () => {
    requireIgnored(
      normalizeSimulatorEvent({
        event_id: 'evt_bad_flyout',
        ts: '2026-01-01T00:00:06Z',
        type: 'tx.updated',
        equivalent: 'UAH',
        amount_flyout: true,
        edges: [],
      }),
      'malformed',
      'invalid_tx_updated_amount_flyout_contract',
    )
  })
})
