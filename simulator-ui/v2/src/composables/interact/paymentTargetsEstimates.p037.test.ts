/**
 * Programme 037, `T3701` - reproducer for F-037-5: `hops` and `max_available` of `payment-targets` are available
 * from the server and are thrown away by the UI.
 *
 * Two halves, because the loss happens in two places (AGENTS.md 1: audit the stage that owns the defect):
 *
 *  1. WIRE - `useInteractActions.fetchPaymentTargets` never asks for `include_max_available=true`
 *     (`simulatorApi.ts:362-369`). Observed at the `fetch` boundary, so the test does not depend on how the
 *     fix names the option.
 *  2. CACHE - `useInteractDataCache.refreshPaymentTargets` reduces the answer to `Set<to_pid>`
 *     (`useInteractDataCache.ts:289,344`). The accessor the fix will add is not named in the spec, so the test
 *     reads what the EXISTING accessor (`paymentTargetsByKey`) holds for the key and accepts either of the two
 *     natural shapes - a `Map` keyed by `to_pid`, or an array of `{to_pid, ...}` rows. A `Set` of ids is neither.
 *
 * Controls (green now): the target ids are still delivered (the To list does not regress), a request without
 * the flag is NOT mistaken for a request with it (anti-vacuum of half 1), and `null` is kept distinct from
 * the measured zero (R037-CAPACITY: "the server did not estimate" is not "0.00").
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { effectScope, ref } from 'vue'

import type { GraphSnapshot } from '../../types'
import { useInteractActions } from '../useInteractActions'
import { useInteractDataCache } from './useInteractDataCache'

type CacheActions = Parameters<typeof useInteractDataCache>[0]['actions']

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('F-037-5 wire: payment-targets is requested with include_max_available=true', () => {
  function stubFetch(items: unknown[]) {
    const urls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: unknown) => {
        urls.push(String(input))
        return new Response(JSON.stringify({ items }), { status: 200, headers: { 'content-type': 'application/json' } })
      }),
    )
    return urls
  }

  it('REPRODUCER (red now): the outgoing request carries include_max_available=true', async () => {
    const urls = stubFetch([{ to_pid: 'bob', hops: 2, max_available: '12.50' }])
    const ia = useInteractActions({
      httpConfig: ref({ apiBase: 'http://example.test', accessToken: 'x' }),
      runId: ref('run_p037'),
    })

    const items = await ia.fetchPaymentTargets('UAH', 'alice', 6)

    expect(urls, 'precondition: exactly one request reached the wire').toHaveLength(1)
    const q = new URL(urls[0]!).searchParams
    expect(q.get('from_pid'), 'precondition: it is the payment-targets request').toBe('alice')
    expect(
      q.get('include_max_available'),
      `F-037-5: UI asks payment-targets without the estimate flag; url was ${urls[0]}`,
    ).toBe('true')
    // The decoder keeps the field (api/simulatorContracts.ts:574-578), so the loss is not on the way in.
    expect(items[0]).toMatchObject({ to_pid: 'bob', hops: 2, max_available: '12.50' })
  })

  it('CONTROL (green now): the parameter reader is not vacuous - from_pid/max_hops are read from the same url', async () => {
    const urls = stubFetch([])
    const ia = useInteractActions({
      httpConfig: ref({ apiBase: 'http://example.test', accessToken: 'x' }),
      runId: ref('run_p037'),
    })
    await ia.fetchPaymentTargets('UAH', 'alice', 8)
    const q = new URL(urls[0]!).searchParams
    expect(q.get('max_hops')).toBe('8')
    expect(q.get('equivalent')).toBe('UAH')
    expect(q.has('include_max_available')).toBe(false)
  })
})

describe('F-037-5 cache: hops and max_available survive per target', () => {
  function mk(items: unknown[]) {
    const fetchPaymentTargets = vi.fn(async () => items) as unknown as CacheActions['fetchPaymentTargets']
    const actions = {
      actionsDisabled: ref(false),
      sendPayment: vi.fn(),
      createTrustline: vi.fn(),
      updateTrustline: vi.fn(),
      closeTrustline: vi.fn(),
      runClearing: vi.fn(),
      fetchParticipants: vi.fn(async () => []),
      fetchTrustlines: vi.fn(async () => []),
      fetchPaymentTargets,
    } as unknown as CacheActions
    const runId = ref('run_p037')
    const equivalent = ref('UAH')
    const snapshot = ref<GraphSnapshot | null>(null)
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
    return { cache, scope }
  }

  /** What the cache holds for `bob` under the key of (run, UAH, alice, 6), whatever the container is. */
  function recordFor(cache: ReturnType<typeof mk>['cache'], pid: string): Record<string, unknown> | undefined {
    const key = cache.paymentTargetsKey({ runId: 'run_p037', eq: 'UAH', fromPid: 'alice', maxHops: 6 })
    const entry: unknown = cache.paymentTargetsByKey.value.get(key)
    if (entry instanceof Map) return entry.get(pid) as Record<string, unknown> | undefined
    if (Array.isArray(entry)) return entry.find((r) => r?.to_pid === pid)
    return undefined // a Set<string> carries ids only
  }

  it('REPRODUCER (red now): the cache keeps hops and max_available of each target', async () => {
    const { cache, scope } = mk([
      { to_pid: 'bob', hops: 2, max_available: '12.50' },
      { to_pid: 'carol', hops: 1, max_available: null },
    ])
    await cache.refreshPaymentTargets({ fromPid: 'alice', maxHops: 6 })
    const bob = recordFor(cache, 'bob')
    expect(bob?.hops, 'F-037-5: `hops` of a target is dropped by the cache (Set<to_pid>)').toBe(2)
    expect(bob?.max_available, 'F-037-5: `max_available` of a target is dropped by the cache').toBe('12.50')
    scope.stop()
  })

  it('REPRODUCER (red now): "the server did not estimate" (null) is kept distinct from the measured zero', async () => {
    const { cache, scope } = mk([
      { to_pid: 'bob', hops: 1, max_available: '0.00' },
      { to_pid: 'carol', hops: 1, max_available: null },
    ])
    await cache.refreshPaymentTargets({ fromPid: 'alice', maxHops: 6 })
    const bob = recordFor(cache, 'bob')
    const carol = recordFor(cache, 'carol')
    expect(bob?.max_available, 'measured zero must survive as the string "0.00"').toBe('0.00')
    expect(carol, 'precondition: carol is in the cache').toBeDefined()
    expect(carol?.max_available, 'null must survive as null, not be coerced to 0 or dropped').toBeNull()
    scope.stop()
  })

  it('CONTROL (green now): the reachable target ids are still delivered to the To list', async () => {
    const { cache, scope } = mk([
      { to_pid: 'bob', hops: 2, max_available: '12.50' },
      { to_pid: 'carol', hops: 1, max_available: null },
    ])
    await cache.refreshPaymentTargets({ fromPid: 'alice', maxHops: 6 })
    const key = cache.paymentTargetsKey({ runId: 'run_p037', eq: 'UAH', fromPid: 'alice', maxHops: 6 })
    const entry: unknown = cache.paymentTargetsByKey.value.get(key)
    const ids = entry instanceof Set ? entry : entry instanceof Map ? new Set(entry.keys()) : new Set(((entry as Array<{ to_pid: string }>) ?? []).map((r) => r.to_pid))
    expect(Array.from(ids).sort()).toEqual(['bob', 'carol'])
    scope.stop()
  })
})
