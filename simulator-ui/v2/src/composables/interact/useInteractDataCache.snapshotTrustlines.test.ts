import { describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, ref } from 'vue'

import type { GraphSnapshot } from '../../types'
import { parseAmountStringOrNull } from '../../utils/numberFormat'
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

function mk(
  snapshotValue: GraphSnapshot,
  fetchTrustlinesImpl?: CacheActions['fetchTrustlines'],
) {
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
    fetchTrustlines: vi.fn<CacheActions['fetchTrustlines']>(
      fetchTrustlinesImpl ?? (async () => null as unknown as TrustlinesResult),
    ),
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
      parseAmountStringOrNull,
    }),
  )!

  return { actions, snapshot, cache, scope }
}

describe('useInteractDataCache: snapshot→trustlines mapping', () => {

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
      limit: '10.00',
      used: '0.00',
      reverse_used: '0.01',
      available: '9.99',
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

  it('normalizes valid snapshot amounts and preserves non-empty malformed source values', async () => {
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
          trust_limit: ' 10,50 ',
          used: 0,
          available: ' 10.50 ',
          status: 'active',
        },
        {
          source: 'bob',
          target: 'alice',
          trust_limit: ' invalid-limit ',
          used: '1e3',
          available: 'unknown',
          status: 'active',
        },
      ],
    })

    await nextTick()

    expect(cache.trustlines.value[0]).toMatchObject({
      limit: '10.50',
      used: '0',
      available: '10.50',
    })
    expect(cache.trustlines.value[1]).toMatchObject({
      limit: 'invalid-limit',
      used: '1e3',
      available: 'unknown',
    })

    scope.stop()
  })
})

/**
 * `F-013-7` — СОСТОЯНИЕ ИСТОЧНИКА В САМОМ КЭШЕ.
 *
 * Почему этого не хватает в тестах корня: там `useInteractMode` замокан, и состояние источника
 * выставляет тест. Здесь судится то, что НАСТОЯЩИЙ кэш выводит это состояние верно — иначе
 * гард будет зелён в тестах и выключен в проде.
 */
describe('useInteractDataCache: trustlines source state (`F-013-7`)', () => {
  const SNAPSHOT: GraphSnapshot = {
    equivalent: 'UAH',
    generated_at: '2026-01-01T00:00:00Z',
    nodes: [
      { id: 'alice', name: 'Alice' },
      { id: 'bob', name: 'Bob' },
    ],
    links: [
      { source: 'alice', target: 'bob', trust_limit: '100', used: '0', available: '100', status: 'active' },
    ],
  } as unknown as GraphSnapshot

  it('без ответа источника состояние — never-asked, а снапшотная строка не считается ответом', async () => {
    // Та же заглушка, что в соседнем блоке: ответ не массив, то есть кэш его не принимает.
    const { cache, scope } = mk(SNAPSHOT)
    await nextTick()
    await Promise.resolve()

    // Слитый список НЕПУСТ — в нём строка из снапшота. Именно так гард и обманывался.
    expect(cache.trustlines.value.length).toBe(1)
    expect(cache.findActiveTrustline('alice', 'bob')).toBeTruthy()

    expect(cache.trustlinesFetchState.value).toEqual({ kind: 'never-asked' })
    expect(
      cache.findAnsweredTrustline('alice', 'bob'),
      'строка из снапшотного фоллбэка выдана за ответ источника',
    ).toBeNull()

    scope.stop()
  })

  it('пустой ответ — это answered, а не never-asked', async () => {
    const { cache, scope } = mk(SNAPSHOT, async () => [] as unknown as TrustlinesResult)
    await nextTick()
    await Promise.resolve()
    await nextTick()

    expect(cache.trustlinesFetchState.value).toEqual({ kind: 'answered' })
    expect(cache.findAnsweredTrustline('alice', 'bob')).toBeNull()
    // При пустом ОТВЕТЕ слитый список остаётся пустым — фоллбэк больше не применяется.
    expect(cache.trustlines.value.length).toBe(0)

    scope.stop()
  })

  it('ответ со строкой виден как ответ именно этой пары', async () => {
    const { cache, scope } = mk(SNAPSHOT, async () => [
      {
        from_pid: 'alice',
        from_name: 'Alice',
        to_pid: 'bob',
        to_name: 'Bob',
        equivalent: 'UAH',
        limit: '42',
        used: '0',
        available: '42',
        status: 'active',
      },
    ] as unknown as TrustlinesResult)
    await nextTick()
    await Promise.resolve()
    await nextTick()

    expect(cache.trustlinesFetchState.value).toEqual({ kind: 'answered' })
    expect(cache.findAnsweredTrustline('alice', 'bob')?.limit).toBe('42')
    expect(cache.findAnsweredTrustline('bob', 'alice')).toBeNull()

    scope.stop()
  })

  it('упавший запрос — это failed с текстом, а не молчаливый фоллбэк', async () => {
    const { cache, scope } = mk(SNAPSHOT, async () => {
      throw new Error('GET /trustlines failed: 503')
    })
    await nextTick()
    await Promise.resolve()
    await nextTick()

    expect(cache.trustlinesFetchState.value).toEqual({ kind: 'failed', message: 'GET /trustlines failed: 503' })
    expect(cache.findAnsweredTrustline('alice', 'bob')).toBeNull()
    // А вот для ПРОСМОТРА снапшотный фоллбэк сохраняется — это так и задумано.
    expect(cache.trustlines.value.length).toBe(1)

    scope.stop()
  })
})
