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
      fetchParticipants: vi.fn(async () => []),
      fetchTrustlines: vi.fn(async () => []),
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
      links: [{ source: 'alice', target: 'bob', used: '0.00', available: '10.00', status: 'active' }],
    })

    const actions = mkActions()
    const im = useInteractMode({ actions: actions as any, equivalent: computed(() => 'UAH'), snapshot })

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
    const im = useInteractMode({ actions: actions as any, equivalent: computed(() => 'UAH'), snapshot })

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
    const im = useInteractMode({ actions: actions as any, equivalent: computed(() => 'UAH'), snapshot })

    im.startClearingFlow()
    expect(im.phase.value).toBe('confirm-clearing')

    // Force an error state.
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

    const im = useInteractMode({ actions: actions as any, equivalent: computed(() => 'UAH'), snapshot })

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

    d.reject(new Error('boom'))
    await p

    expect(im.busy.value).toBe(false)
    expect(im.phase.value).toBe('idle')
    expect(im.state.error).toBe(null)
  })

  it('clearing flow: confirm -> preview (>=800ms) -> running -> idle', async () => {
    vi.useFakeTimers()
    try {
      const snapshot = ref<GraphSnapshot | null>(null)
      const actions = mkActions()
      actions.runClearing.mockResolvedValueOnce({ ok: true, cycles: [], total_cleared_amount: '0.00' } as any)

      const im = useInteractMode({ actions: actions as any, equivalent: computed(() => 'UAH'), snapshot })

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
})

