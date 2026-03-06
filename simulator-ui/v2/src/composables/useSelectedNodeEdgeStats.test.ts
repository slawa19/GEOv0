import { describe, expect, it } from 'vitest'
import type { GraphSnapshot } from '../types'
import { useSelectedNodeEdgeStats } from './useSelectedNodeEdgeStats'

function makeSnapshot(): GraphSnapshot {
  return {
    equivalent: 'UAH',
    generated_at: '2026-01-25T00:00:00Z',
    nodes: [
      { id: 'A', name: 'A' },
      { id: 'B', name: 'B' },
      { id: 'C', name: 'C' },
    ],
    links: [
      { source: 'A', target: 'B', trust_limit: 10 },
      { source: 'C', target: 'A', trust_limit: 5 },
      { source: 'A', target: 'C', trust_limit: 3 },
    ],
  }
}

describe('useSelectedNodeEdgeStats', () => {
  it('computes in/out limits and degree for selected node', () => {
    const snapshot = makeSnapshot()
    const { selectedNodeEdgeStats } = useSelectedNodeEdgeStats({
      getSnapshot: () => snapshot,
      getSelectedNodeId: () => 'A',
    })

    expect(selectedNodeEdgeStats.value).toEqual({
      inLimitText: '5.00',
      outLimitText: '13.00',
      degree: 3,
    })
  })

  it('returns null when snapshot or selected id missing', () => {
    const { selectedNodeEdgeStats } = useSelectedNodeEdgeStats({
      getSnapshot: () => null,
      getSelectedNodeId: () => 'A',
    })
    expect(selectedNodeEdgeStats.value).toBeNull()
  })
})
