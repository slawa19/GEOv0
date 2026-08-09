import { describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, ref } from 'vue'

import type { GraphSnapshot } from '../../types'
import { useInteractDataCache } from './useInteractDataCache'

type CacheActions = Parameters<typeof useInteractDataCache>[0]['actions']
type PaymentTargetsResult = Awaited<ReturnType<CacheActions['fetchPaymentTargets']>>
type ParticipantsResult = Awaited<ReturnType<CacheActions['fetchParticipants']>>
type TrustlinesResult = Awaited<ReturnType<CacheActions['fetchTrustlines']>>
type MockedCacheActions = CacheActions & {
  fetchParticipants: ReturnType<typeof vi.fn<CacheActions['fetchParticipants']>>
  fetchTrustlines: ReturnType<typeof vi.fn<CacheActions['fetchTrustlines']>>
  fetchPaymentTargets: ReturnType<typeof vi.fn<CacheActions['fetchPaymentTargets']>>
}

describe('useInteractDataCache: payment-targets cache TTL/refresh-policy', () => {
  function mk() {
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
      fetchTrustlines: vi.fn<CacheActions['fetchTrustlines']>(async () => [] as TrustlinesResult),
      fetchPaymentTargets: vi.fn<CacheActions['fetchPaymentTargets']>(async () => [] as PaymentTargetsResult),
    }

    const runId = ref('run_test')
    const equivalent = ref('UAH')
    const snapshot = ref<GraphSnapshot | null>(null)

    const scope = effectScope()
    const cache = scope.run(() =>
      useInteractDataCache({
        actions,
        runId,
        equivalent,
        snapshot,
        // Not used by payment-targets logic, but required by the composable.
        parseAmountStringOrNull: (v: unknown) => {
          const s = String(v ?? '').trim()
          return s ? s : null
        },
      }),
    )!

    return { actions, runId, equivalent, snapshot, cache, scope }
  }

  it('TTL: does not refetch within TTL window; refetches after TTL', async () => {
    vi.useFakeTimers()
    try {
      const base = new Date('2026-01-01T00:00:00Z')
      vi.setSystemTime(base)

      const { actions, cache, scope } = mk()
      actions.fetchPaymentTargets.mockResolvedValue([{ to_pid: 'bob', hops: 1 }] as PaymentTargetsResult)

      await cache.refreshPaymentTargets({ fromPid: 'alice', maxHops: 1 })
      expect(actions.fetchPaymentTargets).toHaveBeenCalledTimes(1)

      // Cached + within TTL => no re-fetch.
      await cache.refreshPaymentTargets({ fromPid: 'alice', maxHops: 1 })
      expect(actions.fetchPaymentTargets).toHaveBeenCalledTimes(1)

      // After TTL => revalidate.
      vi.setSystemTime(new Date(base.getTime() + 10_001))
      await cache.refreshPaymentTargets({ fromPid: 'alice', maxHops: 1 })
      expect(actions.fetchPaymentTargets).toHaveBeenCalledTimes(2)

      scope.stop()
    } finally {
      vi.useRealTimers()
    }
  })

  it('refresh trigger: snapshot generated_at change invalidates cache (next refresh refetches)', async () => {
    const { actions, cache, snapshot, scope } = mk()
    actions.fetchPaymentTargets.mockResolvedValue([{ to_pid: 'bob', hops: 1 }] as PaymentTargetsResult)

    await cache.refreshPaymentTargets({ fromPid: 'alice', maxHops: 1 })
    expect(actions.fetchPaymentTargets).toHaveBeenCalledTimes(1)

    // Graph semantics changed => cache cleared.
    snapshot.value = {
      equivalent: 'UAH',
      generated_at: '2026-01-01T00:00:01Z',
      nodes: [],
      links: [],
    }
    await nextTick()

    await cache.refreshPaymentTargets({ fromPid: 'alice', maxHops: 1 })
    expect(actions.fetchPaymentTargets).toHaveBeenCalledTimes(2)

    scope.stop()
  })

  it('run switch publishes new participants even when the old fetch resolves last', async () => {
    const { actions, runId, cache, scope } = mk()
    await nextTick()
    actions.fetchParticipants.mockClear()
    let resolveOld!: (value: ParticipantsResult) => void
    const oldFetch = new Promise<ParticipantsResult>((resolve) => {
      resolveOld = resolve
    })
    actions.fetchParticipants
      .mockImplementationOnce(async () => await oldFetch)
      .mockResolvedValue([{ pid: 'new', name: 'New', type: 'person', status: 'active' }] as ParticipantsResult)

    const oldRefresh = cache.refreshParticipants({ force: true })
    runId.value = 'run_new'
    await nextTick()
    await Promise.resolve()
    expect(cache.participants.value.map((item) => item.pid)).toEqual(['new'])

    resolveOld([{ pid: 'old', name: 'Old', type: 'person', status: 'active' }] as ParticipantsResult)
    await oldRefresh
    expect(cache.participants.value.map((item) => item.pid)).toEqual(['new'])
    scope.stop()
  })

  it('run switch ignores late trustline success/error/loading from the old run', async () => {
    const { actions, runId, cache, scope } = mk()
    await nextTick()
    actions.fetchTrustlines.mockClear()
    let rejectOld!: (reason: unknown) => void
    const oldFetch = new Promise<TrustlinesResult>((_resolve, reject) => {
      rejectOld = reject
    })
    const newTrustline = {
      from_pid: 'new-a',
      from_name: 'New A',
      to_pid: 'new-b',
      to_name: 'New B',
      equivalent: 'UAH',
      limit: '10',
      used: '0',
      available: '10',
      status: 'active',
    }
    actions.fetchTrustlines
      .mockImplementationOnce(async () => await oldFetch)
      .mockResolvedValue([newTrustline] as TrustlinesResult)

    const oldRefresh = cache.refreshTrustlines({ force: true })
    expect(cache.trustlinesLoading.value).toBe(true)
    runId.value = 'run_new'
    await nextTick()
    await Promise.resolve()
    expect(cache.trustlines.value.map((item) => item.from_pid)).toEqual(['new-a'])
    expect(cache.trustlinesLoading.value).toBe(false)

    rejectOld(new Error('old run failed last'))
    await oldRefresh
    expect(cache.trustlines.value.map((item) => item.from_pid)).toEqual(['new-a'])
    expect(cache.trustlinesLastError.value).toBeNull()
    expect(cache.trustlinesLoading.value).toBe(false)
    scope.stop()
  })
})

