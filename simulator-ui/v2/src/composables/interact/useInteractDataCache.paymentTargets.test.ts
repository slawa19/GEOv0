import { describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, ref } from 'vue'

import type { GraphSnapshot } from '../../types'
import { useInteractDataCache } from './useInteractDataCache'

describe('useInteractDataCache: payment-targets cache TTL/refresh-policy', () => {
  function mk() {
    const actions = {
      fetchParticipants: vi.fn(async () => [] as any[]),
      fetchTrustlines: vi.fn(async () => [] as any[]),
      fetchPaymentTargets: vi.fn(async () => [] as any[]),
    }

    const runId = ref('run_test')
    const equivalent = ref('UAH')
    const snapshot = ref<GraphSnapshot | null>(null)

    const scope = effectScope()
    const cache = scope.run(() =>
      useInteractDataCache({
        actions: actions as any,
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
      actions.fetchPaymentTargets.mockResolvedValue([{ to_pid: 'bob' }] as any)

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
    actions.fetchPaymentTargets.mockResolvedValue([{ to_pid: 'bob' }] as any)

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
})

