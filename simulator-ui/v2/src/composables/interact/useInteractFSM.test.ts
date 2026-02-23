import { ref } from 'vue'
import { describe, expect, it } from 'vitest'

import type { GraphSnapshot } from '../../types'
import { useInteractFSM } from './useInteractFSM'
import { keyEdge } from '../../utils/edgeKey'

describe('useInteractFSM', () => {
  /** Minimal stub opts — snapshot is empty, no active trustlines. */
  function makeOpts() {
    return {
      snapshot: ref<GraphSnapshot | null>(null),
      findActiveTrustline: () => null,
    }
  }

  describe('resetToIdle()', () => {
    it('clears edgeAnchor and resets phase to idle', () => {
      const { state, resetToIdle, selectEdge } = useInteractFSM(makeOpts())

      // Transition to editing-trustline with a non-null anchor (simulates canvas edge click).
      selectEdge(keyEdge('node-A', 'node-B'), { x: 100, y: 200 })

      expect(state.phase).toBe('editing-trustline')
      expect(state.edgeAnchor).not.toBeNull()

      // Cancel / reset.
      resetToIdle()

      expect(state.edgeAnchor).toBeNull()
      expect(state.phase).toBe('idle')
    })

    it('preserves lastClearing value after reset (intentional — used by HistoryLog)', () => {
      const { state, resetToIdle, setLastClearing, selectEdge } = useInteractFSM(makeOpts())

      // Set a fake clearing result before starting any flow.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const fakeClearing = { cycles: 3 } as any
      setLastClearing(fakeClearing)

      // Move to a non-idle phase with an anchor.
      selectEdge(keyEdge('node-A', 'node-B'), { x: 10, y: 20 })
      expect(state.phase).toBe('editing-trustline')

      // Reset to idle.
      resetToIdle()

      // lastClearing must NOT be reset — it is preserved for history display.
      // Use toStrictEqual instead of toBe: Vue reactive() wraps the object in a Proxy,
      // so Object.is identity fails; deep equality is the correct assertion here.
      expect(state.lastClearing).toStrictEqual(fakeClearing)
      expect(state.phase).toBe('idle')
    })
  })

  it('selectEdge defaults anchor to null', () => {
    const { state, selectEdge } = useInteractFSM(makeOpts())

    selectEdge(keyEdge('node-A', 'node-B'))
    expect(state.edgeAnchor).toBeNull()
    expect(state.selectedEdgeKey).toBe(keyEdge('node-A', 'node-B'))
  })

  it('selectTrustline trims ids and keeps idle if invalid', () => {
    const { state, selectTrustline } = useInteractFSM(makeOpts())

    selectTrustline('  node-A  ', ' node-B ')
    expect(state.fromPid).toBe('node-A')
    expect(state.toPid).toBe('node-B')
    expect(state.selectedEdgeKey).toBe(keyEdge('node-A', 'node-B'))
    expect(state.phase).toBe('editing-trustline')

    // New instance: invalid selection should not transition phase.
    const h2 = useInteractFSM(makeOpts())
    h2.selectTrustline('   ', 'node-B')
    expect(h2.state.fromPid).toBeNull()
    expect(h2.state.toPid).toBe('node-B')
    expect(h2.state.selectedEdgeKey).toBeNull()
    expect(h2.state.phase).toBe('idle')
  })
})
