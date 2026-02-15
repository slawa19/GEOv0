import { describe, expect, it } from 'vitest'
import { ref } from 'vue'

import type { GraphSnapshot } from '../types'
import { useSystemBalance } from './useSystemBalance'

describe('useSystemBalance', () => {
  it('returns all-zero metrics for null snapshot', () => {
    const snapshot = ref<GraphSnapshot | null>(null)
    const { balance } = useSystemBalance(snapshot)
    expect(balance.value).toEqual({
      totalUsed: 0,
      totalAvailable: 0,
      activeTrustlines: 0,
      activeParticipants: 0,
      utilization: 0,
      isClean: true,
    })
  })

  it('parses string amounts and computes utilization', () => {
    const snapshot = ref<GraphSnapshot | null>({
      equivalent: 'UAH',
      generated_at: '2026-01-01T00:00:00Z',
      nodes: [
        { id: 'alice', name: 'Alice', type: 'person', status: 'active' },
        { id: 'bob', name: 'Bob', type: 'person', status: 'active' },
      ],
      links: [
        {
          source: 'alice',
          target: 'bob',
          used: '10.00',
          available: '90.00',
          status: 'active',
        },
      ],
    })

    const { balance } = useSystemBalance(snapshot)
    expect(balance.value.totalUsed).toBe(10)
    expect(balance.value.totalAvailable).toBe(90)
    expect(balance.value.activeTrustlines).toBe(1)
    expect(balance.value.activeParticipants).toBe(2)
    expect(balance.value.utilization).toBeCloseTo(0.1)
    expect(balance.value.isClean).toBe(false)
  })

  it('treats non-numeric values as 0 and becomes clean after clearing', () => {
    const snap: GraphSnapshot = {
      equivalent: 'UAH',
      generated_at: '2026-01-01T00:00:00Z',
      nodes: [
        { id: 'alice', status: 'active' },
        { id: 'bob', status: 'active' },
      ],
      links: [
        { source: 'alice', target: 'bob', used: 'NOPE', available: '100.00', status: 'active' },
      ],
    }
    const snapshot = ref<GraphSnapshot | null>(snap)

    const { balance } = useSystemBalance(snapshot)
    expect(balance.value.totalUsed).toBe(0)
    expect(balance.value.totalAvailable).toBe(100)
    expect(balance.value.isClean).toBe(true)

    // After clearing, used becomes 0 explicitly.
    snapshot.value = {
      ...snap,
      links: [{ source: 'alice', target: 'bob', used: '0.00', available: '100.00', status: 'active' }],
    }
    expect(balance.value.totalUsed).toBe(0)
    expect(balance.value.isClean).toBe(true)
  })
})

