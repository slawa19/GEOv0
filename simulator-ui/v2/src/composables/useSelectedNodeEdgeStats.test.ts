import { describe, expect, it } from 'vitest'
import { useSelectedNodeEdgeStats } from './useSelectedNodeEdgeStats'

function makeSnapshot() {
  return {
    equivalent: 'UAH',
    nodes: [
      { id: 'A', name: 'A' },
      { id: 'B', name: 'B' },
      { id: 'C', name: 'C' },
    ],
    links: [
      { __key: 'A->B', source: 'A', target: 'B', trust_limit: 10 },
      { __key: 'C->A', source: 'C', target: 'A', trust_limit: 5 },
      { __key: 'A->C', source: 'A', target: 'C', trust_limit: 3 },
    ],
  } as any
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
