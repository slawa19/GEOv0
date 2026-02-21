import { computed, createApp, h, nextTick, reactive, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { useInteractMode } from '../composables/useInteractMode'

import ManualPaymentPanel from './ManualPaymentPanel.vue'
import ActionBar from './ActionBar.vue'

describe('Interact Mode UI (components)', () => {
  it('ManualPaymentPanel: renders when phase=confirm-payment', () => {
    const snapshot = ref<any>(null)
    const actions = {
      actionsDisabled: ref(false),
      sendPayment: vi.fn(async () => ({})),
      createTrustline: vi.fn(async () => ({})),
      updateTrustline: vi.fn(async () => ({})),
      closeTrustline: vi.fn(async () => ({})),
      runClearing: vi.fn(async () => ({})),
      fetchParticipants: vi.fn(async () => []),
      fetchTrustlines: vi.fn(async () => []),
    }

    const im = useInteractMode({ actions: actions as any, equivalent: computed(() => 'UAH'), snapshot })

    // Force confirm-payment phase via state machine
    im.startPaymentFlow()
    im.selectNode('alice')
    im.selectNode('bob')

    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp({
      render: () =>
        h(ManualPaymentPanel as any, {
          phase: im.phase.value,
          state: im.state,
          unit: 'UAH',
          availableCapacity: im.availableCapacity.value,
          busy: im.busy.value,
          canSendPayment: im.canSendPayment.value,
          confirmPayment: im.confirmPayment,
          cancel: im.cancel,
        }),
    })
    app.mount(host)

    expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()

    app.unmount()
    host.remove()
  })

  it('ManualPaymentPanel: cancel() closes panel (phase resets to idle)', async () => {
    const snapshot = ref<any>(null)
    const actions = {
      actionsDisabled: ref(false),
      sendPayment: vi.fn(async () => ({})),
      createTrustline: vi.fn(async () => ({})),
      updateTrustline: vi.fn(async () => ({})),
      closeTrustline: vi.fn(async () => ({})),
      runClearing: vi.fn(async () => ({})),
      fetchParticipants: vi.fn(async () => []),
      fetchTrustlines: vi.fn(async () => []),
    }
    const im = useInteractMode({ actions: actions as any, equivalent: computed(() => 'UAH'), snapshot })
    im.startPaymentFlow()
    im.selectNode('alice')
    im.selectNode('bob')
    expect(im.phase.value).toBe('confirm-payment')

    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp({
      render: () =>
        h(ManualPaymentPanel as any, {
          phase: im.phase.value,
          state: im.state,
          unit: 'UAH',
          busy: im.busy.value,
          confirmPayment: im.confirmPayment,
          cancel: im.cancel,
        }),
    })
    app.mount(host)

    const btn = host.querySelector('[data-testid="manual-payment-cancel"]') as HTMLButtonElement | null
    expect(btn).toBeTruthy()
    btn!.click()

    await nextTick()
    expect(im.phase.value).toBe('idle')

    // Panel should be gone or in CSS leave-transition state.
    // (In jsdom, CSS transitions never fire transitionend, so the element stays with
    // leave-active class during the test. We accept both states as correct.)
    await nextTick()
    const panel = host.querySelector('[data-testid="manual-payment-panel"]')
    expect(!panel || panel.classList.contains('panel-slide-leave-active')).toBe(true)

    app.unmount()
    host.remove()
  })

  it('ActionBar: clicking buttons calls corresponding methods', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const startPaymentFlow = vi.fn()
    const startTrustlineFlow = vi.fn()
    const startClearingFlow = vi.fn()

    const app = createApp({
      render: () =>
        h(ActionBar as any, {
          phase: 'idle',
          busy: false,
          actionsDisabled: false,
          runTerminal: false,
          startPaymentFlow,
          startTrustlineFlow,
          startClearingFlow,
        }),
    })

    app.mount(host)

    ;(host.querySelector('[data-testid="actionbar-payment"]') as HTMLButtonElement).click()
    ;(host.querySelector('[data-testid="actionbar-trustline"]') as HTMLButtonElement).click()
    ;(host.querySelector('[data-testid="actionbar-clearing"]') as HTMLButtonElement).click()

    await nextTick()

    expect(startPaymentFlow).toHaveBeenCalledTimes(1)
    expect(startTrustlineFlow).toHaveBeenCalledTimes(1)
    expect(startClearingFlow).toHaveBeenCalledTimes(1)

    app.unmount()
    host.remove()
  })

  it('ManualPaymentPanel: To dropdown excludes selected From (guard)', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'confirm-payment',
      fromPid: null as string | null,
      toPid: null as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as any,
    })

    const participants = [
      { pid: 'alice', name: 'Alice' },
      { pid: 'bob', name: 'Bob' },
    ]

    const setFromPid = vi.fn((pid: string | null) => {
      state.fromPid = pid
    })
    const setToPid = vi.fn((pid: string | null) => {
      state.toPid = pid
    })

    const app = createApp({
      render: () =>
        h(ManualPaymentPanel as any, {
          phase: state.phase,
          state,
          unit: 'UAH',
          participants,
          busy: false,
          confirmPayment: vi.fn(),
          cancel: vi.fn(),
          setFromPid,
          setToPid,
        }),
    })

    app.mount(host)
    await nextTick()

    const fromSel = host.querySelector('#mp-from') as HTMLSelectElement | null
    expect(fromSel).toBeTruthy()
    fromSel!.value = 'alice'
    fromSel!.dispatchEvent(new Event('change'))
    await nextTick()

    const toSel = host.querySelector('#mp-to') as HTMLSelectElement | null
    expect(toSel).toBeTruthy()

    const toOptionValues = Array.from(toSel!.querySelectorAll('option')).map((o) => (o as HTMLOptionElement).value)
    expect(toOptionValues).not.toContain('alice')
    expect(toOptionValues).toContain('bob')

    app.unmount()
    host.remove()
  })
})

