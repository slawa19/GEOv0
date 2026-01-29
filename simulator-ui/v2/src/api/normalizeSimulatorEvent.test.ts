import { describe, expect, it } from 'vitest'
import { normalizeSimulatorEvent } from './normalizeSimulatorEvent'

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

    expect(evt && (evt as any).type).toBe('tx.updated')
    expect((evt as any).edges).toEqual([])
  })
})
