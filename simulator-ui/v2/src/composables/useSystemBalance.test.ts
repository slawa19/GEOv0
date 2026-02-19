import { effectScope, nextTick, ref } from 'vue'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'

import { useSystemBalance } from './useSystemBalance'
import type { GraphSnapshot } from '../types'

// ----------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------

function makeLink(
  source: string,
  target: string,
  opts: { used?: string; available?: string; status?: string } = {},
): any {
  return {
    source,
    target,
    used: opts.used ?? '0',
    available: opts.available ?? '1000',
    amountText: opts.used ?? '0',
    status: opts.status ?? 'active',
    trust_limit: String(Number(opts.used ?? 0) + Number(opts.available ?? 1000)),
  }
}

function makeNode(id: string, opts: { status?: string } = {}): any {
  return { id, name: id, status: opts.status ?? 'active' }
}

function makeSnapshot(
  links: any[] = [],
  nodes: any[] = [],
  equivalent = 'UAH',
): GraphSnapshot {
  return { links, nodes, equivalent } as any
}

// ----------------------------------------------------------------
// Tests
// ----------------------------------------------------------------

describe('useSystemBalance', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('returns zero metrics for null snapshot', () => {
    const snapshot = ref<GraphSnapshot | null>(null)
    const scope = effectScope()
    let balance: ReturnType<typeof useSystemBalance>['balance'] | undefined

    scope.run(() => {
      const result = useSystemBalance(snapshot)
      balance = result.balance
    })

    expect(balance!.value).toEqual({
      totalUsed: 0,
      totalAvailable: 0,
      activeTrustlines: 0,
      activeParticipants: 0,
      utilization: 0,
      isClean: true,
    })

    scope.stop()
  })

  it('returns zero metrics for empty snapshot (no links, no nodes)', () => {
    const snapshot = ref<GraphSnapshot | null>(makeSnapshot([], []))
    const scope = effectScope()
    let balance: ReturnType<typeof useSystemBalance>['balance'] | undefined

    scope.run(() => {
      balance = useSystemBalance(snapshot).balance
    })

    const b = balance!.value
    expect(b.totalUsed).toBe(0)
    expect(b.totalAvailable).toBe(0)
    expect(b.activeTrustlines).toBe(0)
    expect(b.activeParticipants).toBe(0)
    expect(b.utilization).toBe(0)
    expect(b.isClean).toBe(true)

    scope.stop()
  })

  it('computes totalUsed and totalAvailable from active links', () => {
    const links = [
      makeLink('alice', 'shop', { used: '300', available: '700' }),
      makeLink('shop', 'farmer', { used: '150', available: '850' }),
    ]
    const nodes = [makeNode('alice'), makeNode('shop'), makeNode('farmer')]
    const snapshot = ref<GraphSnapshot | null>(makeSnapshot(links, nodes))

    const scope = effectScope()
    let balance: ReturnType<typeof useSystemBalance>['balance'] | undefined

    scope.run(() => {
      balance = useSystemBalance(snapshot).balance
    })

    const b = balance!.value
    expect(b.totalUsed).toBeCloseTo(450, 5)
    expect(b.totalAvailable).toBeCloseTo(1550, 5)
    expect(b.activeTrustlines).toBe(2)
    expect(b.activeParticipants).toBe(3)
    expect(b.isClean).toBe(false)
    // utilization = 450 / (450 + 1550) = 450/2000 = 0.225
    expect(b.utilization).toBeCloseTo(0.225, 5)

    scope.stop()
  })

  it('isClean = true when no debt (totalUsed == 0)', () => {
    const links = [makeLink('alice', 'shop', { used: '0', available: '1000' })]
    const snapshot = ref<GraphSnapshot | null>(makeSnapshot(links, [makeNode('alice'), makeNode('shop')]))

    const scope = effectScope()
    let balance: ReturnType<typeof useSystemBalance>['balance'] | undefined

    scope.run(() => {
      balance = useSystemBalance(snapshot).balance
    })

    expect(balance!.value.isClean).toBe(true)
    expect(balance!.value.totalUsed).toBe(0)

    scope.stop()
  })

  it('ignores closed/inactive links in totals', () => {
    const links = [
      makeLink('alice', 'shop', { used: '500', available: '500', status: 'active' }),
      makeLink('shop', 'alice', { used: '999', available: '1', status: 'closed' }),
    ]
    const nodes = [makeNode('alice'), makeNode('shop')]
    const snapshot = ref<GraphSnapshot | null>(makeSnapshot(links, nodes))

    const scope = effectScope()
    let balance: ReturnType<typeof useSystemBalance>['balance'] | undefined

    scope.run(() => {
      balance = useSystemBalance(snapshot).balance
    })

    const b = balance!.value
    expect(b.activeTrustlines).toBe(1)
    expect(b.totalUsed).toBeCloseTo(500, 5)
    expect(b.totalAvailable).toBeCloseTo(500, 5)

    scope.stop()
  })

  it('ignores inactive nodes in activeParticipants count', () => {
    const links = [makeLink('alice', 'shop', { used: '0', available: '500' })]
    const nodes = [
      makeNode('alice', { status: 'active' }),
      makeNode('shop', { status: 'frozen' }),
      makeNode('bob', { status: 'closed' }),
    ]
    const snapshot = ref<GraphSnapshot | null>(makeSnapshot(links, nodes))

    const scope = effectScope()
    let balance: ReturnType<typeof useSystemBalance>['balance'] | undefined

    scope.run(() => {
      balance = useSystemBalance(snapshot).balance
    })

    expect(balance!.value.activeParticipants).toBe(1)

    scope.stop()
  })

  it('handles non-numeric used/available fields gracefully (treats as 0)', () => {
    const links = [
      { source: 'a', target: 'b', used: 'n/a', available: undefined, status: 'active' },
    ]
    const snapshot = ref<GraphSnapshot | null>(makeSnapshot(links as any, []))

    const scope = effectScope()
    let balance: ReturnType<typeof useSystemBalance>['balance'] | undefined

    scope.run(() => {
      balance = useSystemBalance(snapshot).balance
    })

    const b = balance!.value
    expect(b.totalUsed).toBe(0)
    expect(b.totalAvailable).toBe(0)
    expect(Number.isFinite(b.utilization)).toBe(true)

    scope.stop()
  })

  it('parses string amounts correctly ("300.00" → 300)', () => {
    const links = [makeLink('a', 'b', { used: '300.00', available: '700.00' })]
    const snapshot = ref<GraphSnapshot | null>(makeSnapshot(links, []))

    const scope = effectScope()
    let balance: ReturnType<typeof useSystemBalance>['balance'] | undefined

    scope.run(() => {
      balance = useSystemBalance(snapshot).balance
    })

    expect(balance!.value.totalUsed).toBeCloseTo(300, 5)
    expect(balance!.value.totalAvailable).toBeCloseTo(700, 5)

    scope.stop()
  })

  it('reacts to snapshot update after debounce', async () => {
    const snapshot = ref<GraphSnapshot | null>(null)

    const scope = effectScope()
    let balance: ReturnType<typeof useSystemBalance>['balance'] | undefined

    scope.run(() => {
      balance = useSystemBalance(snapshot).balance
    })

    // Initial: null snapshot → all zeros.
    expect(balance!.value.totalUsed).toBe(0)

    // Update snapshot.
    snapshot.value = makeSnapshot(
      [makeLink('alice', 'shop', { used: '200', available: '800' })],
      [makeNode('alice'), makeNode('shop')],
    )

    // Flush Vue's watcher queue (flush: 'pre' = microtask).
    await nextTick()

    // Before debounce fires — still sees old value (null → 0).
    expect(balance!.value.totalUsed).toBe(0)

    // Advance time past debounce (100ms) → debouncedSnap.value is updated.
    vi.advanceTimersByTime(150)

    // After debounce — computed reflects new snapshot.
    expect(balance!.value.totalUsed).toBeCloseTo(200, 5)
    expect(balance!.value.totalAvailable).toBeCloseTo(800, 5)
    expect(balance!.value.activeTrustlines).toBe(1)

    scope.stop()
  })

  it('after full clearing — totalUsed = 0, isClean = true', () => {
    const links = [makeLink('alice', 'shop', { used: '0', available: '1000' })]
    const snapshot = ref<GraphSnapshot | null>(
      makeSnapshot(links, [makeNode('alice'), makeNode('shop')]),
    )

    const scope = effectScope()
    let balance: ReturnType<typeof useSystemBalance>['balance'] | undefined

    scope.run(() => {
      balance = useSystemBalance(snapshot).balance
    })

    expect(balance!.value.isClean).toBe(true)
    expect(balance!.value.totalUsed).toBe(0)
    expect(balance!.value.utilization).toBe(0)

    scope.stop()
  })

  it('utilization = 0 when no links (avoid division by zero)', () => {
    const snapshot = ref<GraphSnapshot | null>(makeSnapshot([], []))

    const scope = effectScope()
    let balance: ReturnType<typeof useSystemBalance>['balance'] | undefined

    scope.run(() => {
      balance = useSystemBalance(snapshot).balance
    })

    expect(balance!.value.utilization).toBe(0)
    expect(Number.isFinite(balance!.value.utilization)).toBe(true)

    scope.stop()
  })
})
