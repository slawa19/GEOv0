import { describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, ref } from 'vue'

import type { GraphSnapshot } from '../../types'
import { useInteractDataCache } from './useInteractDataCache'

describe('useInteractDataCache: snapshot→trustlines mapping', () => {
  function mk(snapshotValue: GraphSnapshot) {
    const actions = {
      fetchParticipants: vi.fn(async () => [] as any[]),
      // Return a non-array so `useInteractDataCache` keeps using snapshot-derived trustlines.
      fetchTrustlines: vi.fn(async () => null as any),
      fetchPaymentTargets: vi.fn(async () => [] as any[]),
    }

    const runId = ref('run_test')
    const equivalent = ref(snapshotValue.equivalent)
    const snapshot = ref<GraphSnapshot | null>(snapshotValue)

    const scope = effectScope()
    const cache = scope.run(() =>
      useInteractDataCache({
        actions: actions as any,
        runId,
        equivalent,
        snapshot,
        parseAmountStringOrNull: (v: unknown) => {
          const s = String(v ?? '').trim()
          return s ? s : null
        },
      }),
    )!

    return { actions, snapshot, cache, scope }
  }

  it('maps reverse_used from snapshot.links when present (14.7)', async () => {
    const { cache, scope } = mk({
      equivalent: 'UAH',
      generated_at: '2026-01-01T00:00:00Z',
      nodes: [
        { id: 'alice', name: 'Alice' },
        { id: 'bob', name: 'Bob' },
      ],
      links: [
        {
          source: 'alice',
          target: 'bob',
          trust_limit: '10.00',
          used: '0.00',
          reverse_used: '0.01',
          available: '9.99',
          status: 'active',
        },
      ],
    })

    await nextTick()

    const tl = cache.trustlines.value[0]!
    expect(tl).toMatchObject({
      from_pid: 'alice',
      to_pid: 'bob',
      equivalent: 'UAH',
      reverse_used: '0.01',
    })

    scope.stop()
  })

  it('does not invent reverse_used when snapshot.links does not have it (known limitation)', async () => {
    const { cache, scope } = mk({
      equivalent: 'UAH',
      generated_at: '2026-01-01T00:00:00Z',
      nodes: [
        { id: 'alice', name: 'Alice' },
        { id: 'bob', name: 'Bob' },
      ],
      links: [
        {
          source: 'alice',
          target: 'bob',
          trust_limit: '10.00',
          used: '0.00',
          available: '10.00',
          status: 'active',
        },
      ],
    })

    await nextTick()

    const tl = cache.trustlines.value[0]!
    expect(tl.reverse_used).toBeUndefined()
    expect(Object.prototype.hasOwnProperty.call(tl, 'reverse_used')).toBe(false)

    scope.stop()
  })
})

