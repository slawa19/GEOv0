import { describe, expect, it } from 'vitest'

import type { SimulatorEvent } from './simulatorTypes'
import { normalizeSimulatorEvent } from './normalizeSimulatorEvent'

function requireEventType<TType extends SimulatorEvent['type']>(
  evt: SimulatorEvent | null,
  type: TType,
): Extract<SimulatorEvent, { type: TType }> {
  expect(evt).toBeTruthy()
  expect(evt?.type).toBe(type)
  return evt as Extract<SimulatorEvent, { type: TType }>
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

    const evt = normalizeSimulatorEvent(raw)
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

    const evt = normalizeSimulatorEvent(raw)
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

    const evt = normalizeSimulatorEvent(raw)
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
})
