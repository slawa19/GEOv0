import { describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, ref } from 'vue'

import type { GraphSnapshot } from '../../types'
import { useInteractDataCache } from './useInteractDataCache'

type CacheActions = Parameters<typeof useInteractDataCache>[0]['actions']
type ParticipantsResult = Awaited<ReturnType<CacheActions['fetchParticipants']>>
type TrustlinesResult = Awaited<ReturnType<CacheActions['fetchTrustlines']>>
type PaymentTargetsResult = Awaited<ReturnType<CacheActions['fetchPaymentTargets']>>
type MockedCacheActions = CacheActions & {
  fetchParticipants: ReturnType<typeof vi.fn<CacheActions['fetchParticipants']>>
  fetchTrustlines: ReturnType<typeof vi.fn<CacheActions['fetchTrustlines']>>
  fetchPaymentTargets: ReturnType<typeof vi.fn<CacheActions['fetchPaymentTargets']>>
}

describe('useInteractDataCache: snapshot→trustlines mapping', () => {
  function mk(snapshotValue: GraphSnapshot) {
    const actions: MockedCacheActions = {
      actionsDisabled: ref(false),
      sendPayment: vi.fn(async () => {
        throw new Error('not used in this test')
      }),
      createTrustline: vi.fn(async () => {
        throw new Error('not used in this test')
      }),
      updateTrustline: vi.fn(async () => {
        throw new Error('not used in this test')
      }),
      closeTrustline: vi.fn(async () => {
        throw new Error('not used in this test')
      }),
      runClearing: vi.fn(async () => {
        throw new Error('not used in this test')
      }),
      fetchParticipants: vi.fn<CacheActions['fetchParticipants']>(async () => [] as ParticipantsResult),
      // Return a non-array so `useInteractDataCache` keeps using snapshot-derived trustlines.
      fetchTrustlines: vi.fn<CacheActions['fetchTrustlines']>(async () => null as unknown as TrustlinesResult),
      fetchPaymentTargets: vi.fn<CacheActions['fetchPaymentTargets']>(async () => [] as PaymentTargetsResult),
    }

    const runId = ref('run_test')
    const equivalent = ref(snapshotValue.equivalent)
    const snapshot = ref<GraphSnapshot | null>(snapshotValue)

    const scope = effectScope()
    const cache = scope.run(() =>
      useInteractDataCache({
        actions,
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

