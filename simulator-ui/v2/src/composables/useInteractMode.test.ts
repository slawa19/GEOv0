import { describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'

import type { GraphSnapshot } from '../types'
import { useInteractMode } from './useInteractMode'

describe('useInteractMode', () => {
  function deferred<T>() {
    let resolve!: (v: T) => void
    let reject!: (e: unknown) => void
    const promise = new Promise<T>((res, rej) => {
      resolve = res
      reject = rej
    })
    return { promise, resolve, reject }
  }

  function mkActions() {
    return {
      sendPayment: vi.fn(async () => ({ ok: true } as any)),
      createTrustline: vi.fn(async () => ({ ok: true } as any)),
      updateTrustline: vi.fn(async () => ({ ok: true } as any)),
      closeTrustline: vi.fn(async () => ({ ok: true } as any)),
      runClearing: vi.fn(async () => ({ ok: true, cycles: [] } as any)),
      // IMPORTANT: keep array literal from becoming `never[]` (breaks mock typing in tests).
      fetchParticipants: vi.fn(async () => [] as any[]),
      fetchTrustlines: vi.fn(async () => [] as any[]),
      fetchPaymentTargets: vi.fn(async () => [] as any[]),
    }
  }

  it('payment flow: idle -> picking -> confirm -> idle', async () => {
    const snapshot = ref<GraphSnapshot | null>({
      equivalent: 'UAH',
      generated_at: '2026-01-01T00:00:00Z',
      nodes: [
        { id: 'alice', name: 'Alice', type: 'person', status: 'active' },
        { id: 'bob', name: 'Bob', type: 'person', status: 'active' },
      ],
      // For payment alice -> bob, capacity is provided by trustline bob -> alice.
      links: [{ source: 'bob', target: 'alice', used: '0.00', available: '10.00', status: 'active' }],
    })

    const actions = mkActions()
    const runId = computed(() => 'run_test')
    const im = useInteractMode({ actions: actions as any, runId, equivalent: computed(() => 'UAH'), snapshot })

    expect(im.phase.value).toBe('idle')
    im.startPaymentFlow()
    expect(im.phase.value).toBe('picking-payment-from')

    im.selectNode('alice')
    expect(im.phase.value).toBe('picking-payment-to')
    expect(im.state.fromPid).toBe('alice')

    im.selectNode('bob')
    expect(im.phase.value).toBe('confirm-payment')
    expect(im.state.toPid).toBe('bob')
    expect(im.availableCapacity.value).toBe('10.00')
    expect(im.canSendPayment.value).toBe(true)

    await im.confirmPayment('1.00')
    expect(actions.sendPayment).toHaveBeenCalledWith('alice', 'bob', '1.00', 'UAH')
    expect(im.successMessage.value).toBe('Payment sent: 1.00 UAH')
    expect(im.phase.value).toBe('idle')
    expect(im.busy.value).toBe(false)
  })

  it('trustline flow: goes to editing-trustline when link exists', () => {
    const snapshot = ref<GraphSnapshot | null>({
      equivalent: 'UAH',
      generated_at: '2026-01-01T00:00:00Z',
      nodes: [
        { id: 'alice', status: 'active' },
        { id: 'bob', status: 'active' },
      ],
      links: [{ source: 'alice', target: 'bob', used: '1.00', available: '9.00', status: 'active' }],
    })

    const actions = mkActions()
    const runId = computed(() => 'run_test')
    const im = useInteractMode({ actions: actions as any, runId, equivalent: computed(() => 'UAH'), snapshot })

    im.startTrustlineFlow()
    expect(im.phase.value).toBe('picking-trustline-from')
    im.selectNode('alice')
    expect(im.phase.value).toBe('picking-trustline-to')
    im.selectNode('bob')
    expect(im.phase.value).toBe('editing-trustline')
  })

  it('cancel resets to idle and clears error', async () => {
    const snapshot = ref<GraphSnapshot | null>(null)
    const actions = mkActions()
    actions.sendPayment.mockRejectedValueOnce(new Error('boom'))
    const runId = computed(() => 'run_test')
    const im = useInteractMode({ actions: actions as any, runId, equivalent: computed(() => 'UAH'), snapshot })

    // Force an error state (failed payment confirm).
    im.startPaymentFlow()
    im.selectNode('alice')
    im.selectNode('bob')
    await im.confirmPayment('1.00')
    expect(im.state.error).toBe('boom')

    im.cancel()
    expect(im.phase.value).toBe('idle')
    expect(im.state.error).toBe(null)
    expect(im.state.fromPid).toBe(null)
  })

  it('cancel keeps busy=true until in-flight action settles; cancelled error does not leak', async () => {
    const snapshot = ref<GraphSnapshot | null>(null)
    const actions = mkActions()

    const d = deferred<any>()
    actions.sendPayment.mockImplementationOnce(() => d.promise)

    const runId = computed(() => 'run_test')
    const im = useInteractMode({ actions: actions as any, runId, equivalent: computed(() => 'UAH'), snapshot })

    im.startPaymentFlow()
    im.selectNode('alice')
    im.selectNode('bob')

    const p = im.confirmPayment('1.00')
    await Promise.resolve() // let runBusy set busy + start the async action

    expect(im.busy.value).toBe(true)

    im.cancel()
    expect(im.phase.value).toBe('idle')
    expect(im.state.error).toBe(null)
    // chosen model: cancel doesn't clear busy while promise is in-flight
    expect(im.busy.value).toBe(true)
    expect(im.cancelling.value).toBe(true)

    d.reject(new Error('boom'))
    await p

    expect(im.busy.value).toBe(false)
    expect(im.cancelling.value).toBe(false)
    expect(im.phase.value).toBe('idle')
    expect(im.state.error).toBe(null)
  })

  it('clearing flow: confirm -> preview (>=800ms) -> running -> idle', async () => {
    vi.useFakeTimers()
    try {
      const snapshot = ref<GraphSnapshot | null>(null)
      const actions = mkActions()
      actions.runClearing.mockResolvedValueOnce({ ok: true, cycles: [], total_cleared_amount: '0.00' } as any)

      const runId = computed(() => 'run_test')
      const im = useInteractMode({ actions: actions as any, runId, equivalent: computed(() => 'UAH'), snapshot })

      im.startClearingFlow()
      expect(im.phase.value).toBe('confirm-clearing')

      const p = im.confirmClearing()

      // Immediately switches to preview and becomes busy.
      expect(im.phase.value).toBe('clearing-preview')
      expect(im.busy.value).toBe(true)

      // Even after the API resolves, preview should dwell at least 800ms.
      await Promise.resolve()
      await Promise.resolve()
      expect(actions.runClearing).toHaveBeenCalledTimes(1)
      expect(im.phase.value).toBe('clearing-preview')

      await vi.advanceTimersByTimeAsync(799)
      expect(im.phase.value).toBe('clearing-preview')

      await vi.advanceTimersByTimeAsync(1)
      expect(im.phase.value).toBe('clearing-running')

      // Running state should not immediately disappear in the same tick.
      await vi.advanceTimersByTimeAsync(199)
      expect(im.phase.value).toBe('clearing-running')

      await vi.advanceTimersByTimeAsync(1)
      await p
      expect(im.phase.value).toBe('idle')
      expect(im.busy.value).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  // BUG-11: additional coverage

  it('trustline create flow: picking-from -> picking-to -> confirm-trustline-create', () => {
    const snapshot = ref<GraphSnapshot | null>({
      equivalent: 'UAH',
      generated_at: '2026-01-01T00:00:00Z',
      nodes: [{ id: 'alice', status: 'active' }, { id: 'carol', status: 'active' }],
      links: [], // no existing trustline alice->carol
    })
    const runId = computed(() => 'run_test')
    const im = useInteractMode({ actions: mkActions() as any, runId, equivalent: computed(() => 'UAH'), snapshot })

    im.startTrustlineFlow()
    expect(im.phase.value).toBe('picking-trustline-from')
    im.selectNode('alice')
    expect(im.phase.value).toBe('picking-trustline-to')
    im.selectNode('carol')
    // No existing link → confirm-trustline-create
    expect(im.phase.value).toBe('confirm-trustline-create')
    expect(im.state.fromPid).toBe('alice')
    expect(im.state.toPid).toBe('carol')
  })

  it('setPaymentFromPid and setPaymentToPid update state in picking phases', () => {
    const snapshot = ref<GraphSnapshot | null>(null)
    const runId = computed(() => 'run_test')
    const im = useInteractMode({ actions: mkActions() as any, runId, equivalent: computed(() => 'UAH'), snapshot })

    im.startPaymentFlow()
    expect(im.phase.value).toBe('picking-payment-from')

    im.setPaymentFromPid('alice')
    expect(im.state.fromPid).toBe('alice')
    expect(im.phase.value).toBe('picking-payment-to')

    im.setPaymentToPid('bob')
    expect(im.state.toPid).toBe('bob')
    expect(im.phase.value).toBe('confirm-payment')
  })

  it('selectEdge transitions to editing-trustline phase', () => {
    const snapshot = ref<GraphSnapshot | null>(null)
    const runId = computed(() => 'run_test')
    const im = useInteractMode({ actions: mkActions() as any, runId, equivalent: computed(() => 'UAH'), snapshot })

    // selectEdge should set phase to 'editing-trustline'
    im.selectEdge('alice→bob')
    expect(im.phase.value).toBe('editing-trustline')
    expect(im.state.fromPid).toBe('alice')
    expect(im.state.toPid).toBe('bob')
  })

  it('successMessage is retriggered when the same toast text repeats (microtask reset)', async () => {
    const origQueueMicrotask = (globalThis as any).queueMicrotask as ((fn: () => void) => void) | undefined
    const scheduled: Array<() => void> = []
    ;(globalThis as any).queueMicrotask = (fn: () => void) => {
      scheduled.push(fn)
    }

    const snapshot = ref<GraphSnapshot | null>({
      equivalent: 'UAH',
      generated_at: '2026-01-01T00:00:00Z',
      nodes: [
        { id: 'alice', status: 'active' },
        { id: 'bob', status: 'active' },
      ],
      links: [{ source: 'alice', target: 'bob', used: '0.00', available: '10.00', status: 'active' }],
    })

    const actions = mkActions()
    const runId = computed(() => 'run_test')
    const im = useInteractMode({ actions: actions as any, runId, equivalent: computed(() => 'UAH'), snapshot })

    try {
      im.selectEdge('alice→bob')
      await im.confirmTrustlineClose()

      const msg = 'Trustline closed: alice → bob'
      expect(im.successMessage.value).toBe(msg)

      // Repeat the exact same success message.
      im.selectEdge('alice→bob')
      await im.confirmTrustlineClose()

      // The repeated-message path clears to null, and schedules a microtask to set it back.
      expect(im.successMessage.value).toBe(null)
      expect(scheduled.length).toBeGreaterThanOrEqual(1)

      // Flush scheduled microtasks manually.
      while (scheduled.length) scheduled.shift()?.()
      expect(im.successMessage.value).toBe(msg)
    } finally {
      ;(globalThis as any).queueMicrotask = origQueueMicrotask
    }
  })

  it('MP-6a: startPaymentFlow prefetches trustlines even when cache is warm', async () => {
    const snapshot = ref<GraphSnapshot | null>(null)
    const actions = mkActions()

    const d1 = deferred<any[]>()
    const d2 = deferred<any[]>()
    actions.fetchTrustlines.mockImplementationOnce(() => d1.promise).mockImplementationOnce(() => d2.promise)

    // Creating useInteractMode triggers an immediate trustlines refresh (watch on equivalent).
    const runId = computed(() => 'run_test')
    const im = useInteractMode({ actions: actions as any, runId, equivalent: computed(() => 'UAH'), snapshot })
    expect(actions.fetchTrustlines).toHaveBeenCalledTimes(1)

    d1.resolve([])
    await Promise.resolve()

    // With warm cache, only a forced refresh should call fetchTrustlines again.
    im.startPaymentFlow()
    expect(actions.fetchTrustlines).toHaveBeenCalledTimes(2)

    d2.resolve([])
    await Promise.resolve()
  })

  it('payment-targets: snapshot change forces refresh for current From (prevents forever-stale targets)', async () => {
    const snapshot = ref<GraphSnapshot | null>({
      equivalent: 'UAH',
      generated_at: '2026-01-01T00:00:00Z',
      nodes: [
        { id: 'alice', status: 'active' },
        { id: 'bob', status: 'active' },
      ],
      links: [],
    })

    const actions = mkActions()
    actions.fetchPaymentTargets.mockResolvedValue([{ to_pid: 'bob', hops: 1 }] as any)

    const runId = computed(() => 'run_test')
    const im = useInteractMode({ actions: actions as any, runId, equivalent: computed(() => 'UAH'), snapshot })

    im.startPaymentFlow()
    im.setPaymentFromPid('alice')

    // allow async refreshPaymentTargets() to settle
    await Promise.resolve()
    await Promise.resolve()
    expect(actions.fetchPaymentTargets).toHaveBeenCalledTimes(1)

    // New snapshot tick => must revalidate targets even if TTL would otherwise allow reuse.
    snapshot.value = {
      ...snapshot.value!,
      generated_at: '2026-01-01T00:00:01Z',
    }

    await Promise.resolve()
    await Promise.resolve()
    expect(actions.fetchPaymentTargets).toHaveBeenCalledTimes(2)
  })

  it('availableCapacity computed from snapshot link', () => {
    const snapshot = ref<GraphSnapshot | null>({
      equivalent: 'UAH',
      generated_at: '2026-01-01T00:00:00Z',
      nodes: [{ id: 'alice', status: 'active' }, { id: 'bob', status: 'active' }],
      // For payment alice -> bob, capacity comes from trustline bob -> alice.
      links: [{ source: 'bob', target: 'alice', used: '200', available: '800', status: 'active' }],
    })
    const runId = computed(() => 'run_test')
    const im = useInteractMode({ actions: mkActions() as any, runId, equivalent: computed(() => 'UAH'), snapshot })

    im.startPaymentFlow()
    im.selectNode('alice')
    im.selectNode('bob')
    expect(im.phase.value).toBe('confirm-payment')
    expect(im.availableCapacity.value).toBe('800')
  })

  it('clearing flow with 0 cycles does not error', async () => {
    vi.useFakeTimers()
    try {
      const snapshot = ref<GraphSnapshot | null>(null)
      const actions = mkActions()
      actions.runClearing.mockResolvedValueOnce({
        ok: true,
        cleared_cycles: 0,
        total_cleared_amount: '0.00',
        cycles: [],
        equivalent: 'UAH',
      } as any)

      const clearingDoneCallback = vi.fn()
      const im = useInteractMode({
        actions: actions as any,
        runId: computed(() => 'run_test'),
        equivalent: computed(() => 'UAH'),
        snapshot,
        onClearingDone: clearingDoneCallback,
      })

      im.startClearingFlow()
      const p = im.confirmClearing()

      await vi.advanceTimersByTimeAsync(1100)
      await p

      expect(im.phase.value).toBe('idle')
      expect(clearingDoneCallback).toHaveBeenCalledWith(
        expect.objectContaining({ cleared_cycles: 0, cycles: [] }),
      )
    } finally {
      vi.useRealTimers()
    }
  })

  it('confirmPayment uses resetToIdle (not cancel) on success — epoch not double-incremented', async () => {
    const snapshot = ref<GraphSnapshot | null>(null)
    const actions = mkActions()
    const runId = computed(() => 'run_test')
    const im = useInteractMode({ actions: actions as any, runId, equivalent: computed(() => 'UAH'), snapshot })

    im.startPaymentFlow()
    im.selectNode('alice')
    im.selectNode('bob')

    await im.confirmPayment('10.00')

    // After success: phase=idle, busy=false, no error
    expect(im.phase.value).toBe('idle')
    expect(im.busy.value).toBe(false)
    expect(im.state.error).toBeNull()

    // Subsequent action should work immediately (epoch not corrupted)
    im.startPaymentFlow()
    expect(im.phase.value).toBe('picking-payment-from')
  })

  it('availableTargetIds is an empty Set in idle phase (undefined reserved for loading/unknown only)', () => {
    const snapshot = ref<GraphSnapshot | null>(null)
    const runId = computed(() => 'run_test')
    const im = useInteractMode({ actions: mkActions() as any, runId, equivalent: computed(() => 'UAH'), snapshot })

    const ids = im.availableTargetIds.value
    expect(ids).toBeInstanceOf(Set)
    expect((ids as Set<string>).size).toBe(0)
  })

  it('availableTargetIds is undefined in picking-payment-to while trustlinesLoading=true (unknown)', async () => {
    const snapshot = ref<GraphSnapshot | null>({
      equivalent: 'UAH',
      generated_at: '2026-01-01T00:00:00Z',
      nodes: [
        { id: 'alice', status: 'active' },
        { id: 'bob', status: 'active' },
      ],
      links: [],
    })

    const actions = mkActions()
    const d = deferred<any[]>()
    actions.fetchTrustlines.mockImplementationOnce(() => d.promise)

    const runId = computed(() => 'run_test')
    const im = useInteractMode({ actions: actions as any, runId, equivalent: computed(() => 'UAH'), snapshot })

    im.startPaymentFlow()
    im.setPaymentFromPid('alice')
    expect(im.phase.value).toBe('picking-payment-to')

    // While trustlines fetch is in-flight, highlight targets are unknown.
    expect(im.trustlinesLoading.value).toBe(true)
    expect(im.availableTargetIds.value).toBeUndefined()

    // Cleanup: settle the in-flight promise.
    d.resolve([])
    await Promise.resolve()
  })

  it('availableTargetIds is an empty Set in picking-payment-to when trustlines are known but no direct-hop targets exist', async () => {
    const snapshot = ref<GraphSnapshot | null>({
      equivalent: 'UAH',
      generated_at: '2026-01-01T00:00:00Z',
      nodes: [
        { id: 'alice', status: 'active' },
        { id: 'bob', status: 'active' },
      ],
      links: [],
    })

    const actions = mkActions()
    // Known-empty trustlines list => known-empty highlight set.
    actions.fetchTrustlines.mockResolvedValueOnce([])

    const runId = computed(() => 'run_test')
    const im = useInteractMode({ actions: actions as any, runId, equivalent: computed(() => 'UAH'), snapshot })

    im.startPaymentFlow()
    im.setPaymentFromPid('alice')
    expect(im.phase.value).toBe('picking-payment-to')

    // Allow the fetch promise to settle and loading flag to flip.
    await Promise.resolve()
    await Promise.resolve()

    expect(im.trustlinesLoading.value).toBe(false)
    const ids = im.availableTargetIds.value
    expect(ids).toBeInstanceOf(Set)
    expect((ids as Set<string>).size).toBe(0)
  })

  it('availableTargetIds includes participants (excluding from) in picking-trustline-to', () => {
    const snapshot = ref<GraphSnapshot | null>({
      equivalent: 'UAH',
      generated_at: '2026-01-01T00:00:00Z',
      nodes: [
        { id: 'alice', status: 'active' },
        { id: 'bob', status: 'active' },
        { id: 'carol', status: 'active' },
      ],
      links: [],
    })
    const runId = computed(() => 'run_test')
    const im = useInteractMode({ actions: mkActions() as any, runId, equivalent: computed(() => 'UAH'), snapshot })

    im.startTrustlineFlow()
    im.setTrustlineFromPid('alice')
    expect(im.phase.value).toBe('picking-trustline-to')

    const ids = im.availableTargetIds.value
    expect(ids).toBeInstanceOf(Set)
    if (!ids) throw new Error('expected availableTargetIds to be a Set in picking-trustline-to')
    expect(ids.has('bob')).toBe(true)
    expect(ids.has('carol')).toBe(true)
    expect(ids.has('alice')).toBe(false) // excludes self
  })

  it('does not start an alternative flow when phase is not idle (one flow at a time)', () => {
    const snapshot = ref<GraphSnapshot | null>(null)
    const runId = computed(() => 'run_test')
    const im = useInteractMode({ actions: mkActions() as any, runId, equivalent: computed(() => 'UAH'), snapshot })

    im.startPaymentFlow()
    expect(im.phase.value).toBe('picking-payment-from')

    // Attempt to start a different flow while current flow is active.
    im.startTrustlineFlow()
    expect(im.phase.value).toBe('picking-payment-from')

    im.startClearingFlow()
    expect(im.phase.value).toBe('picking-payment-from')
  })
})


