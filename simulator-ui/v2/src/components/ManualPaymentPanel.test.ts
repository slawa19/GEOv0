import { createApp, h, nextTick, reactive } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import ManualPaymentPanel from './ManualPaymentPanel.vue'

describe('ManualPaymentPanel', () => {
  it('FB-3/UX-9: shows ESC hint in title and wires aria-describedby for To and Amount help', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'confirm-payment',
      fromPid: 'alice',
      toPid: 'bob',
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as any,
    })

    const app = createApp({
      render: () =>
        h(ManualPaymentPanel as any, {
          phase: 'confirm-payment',
          state,

          unit: 'UAH',
          availableCapacity: '10',

          trustlinesLoading: false,
          paymentTargetsLoading: false,
          paymentTargetsLastError: null,
          paymentToTargetIds: new Set(['bob']),
          trustlines: [],

          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
          ],

          setFromPid: vi.fn(),
          setToPid: vi.fn(),

          busy: false,
          canSendPayment: true,
          confirmPayment: vi.fn(),
          cancel: vi.fn(),

          anchor: null,
          hostEl: null,
        }),
    })

    app.mount(host)
    await nextTick()

    expect(host.textContent ?? '').toContain('ESC to close')

    const toSelect = host.querySelector('#mp-to') as HTMLSelectElement
    expect(toSelect.getAttribute('aria-describedby')).toBe('mp-to-help')
    const toHelp = host.querySelector('#mp-to-help') as HTMLElement | null
    expect(toHelp).toBeTruthy()

    const amountInput = host.querySelector('#mp-amount') as HTMLInputElement
    expect(amountInput.getAttribute('aria-describedby')).toBe('mp-amount-help')
    const amountHelp = host.querySelector('#mp-amount-help') as HTMLElement | null
    expect(amountHelp).toBeTruthy()

    app.unmount()
    host.remove()
  })

  it('MP-1b: resets To selection and shows warning when availableTargetIds becomes known and excludes current toPid', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'picking-payment-to',
      fromPid: 'alice' as string | null,
      toPid: 'bob' as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as any,
    })

    const ui = reactive({
      phase: 'picking-payment-to' as const,
      paymentToTargetIds: undefined as Set<string> | undefined,
    })

    const setFromPid = vi.fn((pid: string | null) => {
      state.fromPid = pid
    })
    const setToPid = vi.fn((pid: string | null) => {
      state.toPid = pid
    })

    const app = createApp({
      render: () =>
        h(ManualPaymentPanel as any, {
          phase: ui.phase,
          state,

          unit: 'UAH',
          availableCapacity: null,

          trustlinesLoading: false,
          paymentTargetsLoading: false,
          paymentTargetsLastError: null,
          paymentToTargetIds: ui.paymentToTargetIds,
          trustlines: [],

          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
            { pid: 'carol', name: 'Carol' },
          ],

          setFromPid,
          setToPid,

          busy: false,
          canSendPayment: true,
          confirmPayment: vi.fn(),
          cancel: vi.fn(),

          anchor: null,
          hostEl: null,
        }),
    })

    app.mount(host)
    await nextTick()

    // Unknown => no reset.
    expect(state.toPid).toBe('bob')

    // Become known: bob is no longer reachable.
    ui.paymentToTargetIds = new Set(['carol'])
    await nextTick()
    await nextTick()

    const toSelect = host.querySelector('#mp-to') as HTMLSelectElement
    expect(state.toPid).toBe(null)
    expect(toSelect.value).toBe('')

    const warn = host.querySelector('[data-testid="manual-payment-to-invalid-warn"]') as HTMLElement
    expect(warn).toBeTruthy()
    expect(warn.textContent ?? '').toContain('Selected recipient is no longer available. Please re-select.')

    app.unmount()
    host.remove()
  })

  it("MP-4: normalizes amount before sending ('1,5' -> '1.5')", async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'confirm-payment',
      fromPid: 'alice',
      toPid: 'bob',
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as any,
    })

    const ui = reactive({
      phase: 'confirm-payment' as const,
      paymentToTargetIds: new Set(['bob']),
    })

    const confirmPayment = vi.fn()

    const app = createApp({
      render: () =>
        h(ManualPaymentPanel as any, {
          phase: ui.phase,
          state,

          unit: 'UAH',
          availableCapacity: '10',

          trustlinesLoading: false,
          paymentTargetsLoading: false,
          paymentTargetsLastError: null,
          paymentToTargetIds: ui.paymentToTargetIds,
          trustlines: [],

          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
          ],

          setFromPid: vi.fn(),
          setToPid: vi.fn(),

          busy: false,
          canSendPayment: true,
          confirmPayment,
          cancel: vi.fn(),

          anchor: null,
          hostEl: null,
        }),
    })

    app.mount(host)
    await nextTick()

    const input = host.querySelector('#mp-amount') as HTMLInputElement
    input.value = '1,5'
    input.dispatchEvent(new Event('input'))
    await nextTick()

    const btn = host.querySelector('[data-testid="manual-payment-confirm"]') as HTMLButtonElement
    expect(btn.disabled).toBe(false)
    btn.click()

    expect(confirmPayment).toHaveBeenCalledTimes(1)
    expect(confirmPayment).toHaveBeenCalledWith('1.5')

    app.unmount()
    host.remove()
  })

  it('UX-8: shows inline reason when amount format is invalid', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'confirm-payment',
      fromPid: 'alice',
      toPid: 'bob',
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as any,
    })

    const app = createApp({
      render: () =>
        h(ManualPaymentPanel as any, {
          phase: 'confirm-payment',
          state,

          unit: 'UAH',
          availableCapacity: '10',

          trustlinesLoading: false,
          paymentTargetsLoading: false,
          paymentTargetsLastError: null,
          paymentToTargetIds: new Set(['bob']),
          trustlines: [],

          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
          ],

          setFromPid: vi.fn(),
          setToPid: vi.fn(),

          busy: false,
          canSendPayment: true,
          confirmPayment: vi.fn(),
          cancel: vi.fn(),

          anchor: null,
          hostEl: null,
        }),
    })

    app.mount(host)
    await nextTick()

    const input = host.querySelector('#mp-amount') as HTMLInputElement
    input.value = '1,2,3'
    input.dispatchEvent(new Event('input'))
    await nextTick()

    const reason = host.querySelector('[data-testid="mp-confirm-reason"]') as HTMLElement
    expect(reason).toBeTruthy()
    expect((reason.textContent ?? '').trim()).toBe("Invalid amount format. Use digits and '.' for decimals.")

    const btn = host.querySelector('[data-testid="manual-payment-confirm"]') as HTMLButtonElement
    expect(btn.disabled).toBe(true)

    app.unmount()
    host.remove()
  })

  it('UX-8: shows "Enter a positive amount." for 0', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'confirm-payment',
      fromPid: 'alice',
      toPid: 'bob',
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as any,
    })

    const app = createApp({
      render: () =>
        h(ManualPaymentPanel as any, {
          phase: 'confirm-payment',
          state,

          unit: 'UAH',
          availableCapacity: '10',

          trustlinesLoading: false,
          paymentTargetsLoading: false,
          paymentTargetsLastError: null,
          paymentToTargetIds: new Set(['bob']),
          trustlines: [],

          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
          ],

          setFromPid: vi.fn(),
          setToPid: vi.fn(),

          busy: false,
          canSendPayment: true,
          confirmPayment: vi.fn(),
          cancel: vi.fn(),

          anchor: null,
          hostEl: null,
        }),
    })

    app.mount(host)
    await nextTick()

    const input = host.querySelector('#mp-amount') as HTMLInputElement
    input.value = '0'
    input.dispatchEvent(new Event('input'))
    await nextTick()

    const reason = host.querySelector('[data-testid="mp-confirm-reason"]') as HTMLElement
    expect((reason.textContent ?? '').trim()).toBe('Enter a positive amount.')

    app.unmount()
    host.remove()
  })

  it('AC-MP-3/MP-2: To option shows capacity label when trustlines are provided', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'confirm-payment',
      fromPid: 'alice',
      toPid: null as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as any,
    })

    const app = createApp({
      render: () =>
        h(ManualPaymentPanel as any, {
          phase: 'confirm-payment',
          state,

          unit: 'UAH',
          availableCapacity: null,

          trustlinesLoading: false,
          paymentTargetsLoading: false,
          paymentTargetsLastError: null,
          paymentToTargetIds: new Set(['bob']),
          trustlines: [
            // For payment alice -> bob, capacity is defined by trustline bob -> alice.
            { from_pid: 'bob', to_pid: 'alice', available: '500', status: 'active' },
          ],

          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
          ],

          setFromPid: vi.fn(),
          setToPid: vi.fn(),

          busy: false,
          canSendPayment: true,
          confirmPayment: vi.fn(),
          cancel: vi.fn(),

          anchor: null,
          hostEl: null,
        }),
    })

    app.mount(host)
    await nextTick()

    const opt = host.querySelector('#mp-to option[value="bob"]') as HTMLOptionElement | null
    expect(opt).toBeTruthy()
    expect((opt?.textContent ?? '').trim()).toContain('Bob (bob)')
    expect((opt?.textContent ?? '').trim()).toContain('500 UAH')

    app.unmount()
    host.remove()
  })

  it('AC-MP-5: amount > capacity shows inline warning and disables confirm', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'confirm-payment',
      fromPid: 'alice',
      toPid: 'bob',
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as any,
    })

    const app = createApp({
      render: () =>
        h(ManualPaymentPanel as any, {
          phase: 'confirm-payment',
          state,
          unit: 'UAH',
          availableCapacity: '10',

          trustlinesLoading: false,
          paymentTargetsLoading: false,
          paymentTargetsLastError: null,
          paymentToTargetIds: new Set(['bob']),
          trustlines: [],

          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
          ],

          setFromPid: vi.fn(),
          setToPid: vi.fn(),

          busy: false,
          canSendPayment: true,
          confirmPayment: vi.fn(),
          cancel: vi.fn(),
          anchor: null,
          hostEl: null,
        }),
    })

    app.mount(host)
    await nextTick()

    const input = host.querySelector('#mp-amount') as HTMLInputElement
    input.value = '11'
    input.dispatchEvent(new Event('input'))
    await nextTick()

    const reason = host.querySelector('[data-testid="mp-confirm-reason"]') as HTMLElement
    expect((reason.textContent ?? '').trim()).toContain('Amount exceeds available capacity')
    expect((reason.textContent ?? '').trim()).toContain('max: 10 UAH')

    const btn = host.querySelector('[data-testid="manual-payment-confirm"]') as HTMLButtonElement
    expect(btn.disabled).toBe(true)

    app.unmount()
    host.remove()
  })

  it('MP-4: busy=true does not show confirm disabled reason', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'confirm-payment',
      fromPid: 'alice',
      toPid: 'bob',
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as any,
    })

    const app = createApp({
      render: () =>
        h(ManualPaymentPanel as any, {
          phase: 'confirm-payment',
          state,

          unit: 'UAH',
          availableCapacity: '10',

          trustlinesLoading: false,
          paymentTargetsLoading: false,
          paymentTargetsLastError: null,
          paymentToTargetIds: new Set(['bob']),
          trustlines: [],

          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
          ],

          setFromPid: vi.fn(),
          setToPid: vi.fn(),

          busy: true,
          canSendPayment: true,
          confirmPayment: vi.fn(),
          cancel: vi.fn(),

          anchor: null,
          hostEl: null,
        }),
    })

    app.mount(host)
    await nextTick()

    const input = host.querySelector('#mp-amount') as HTMLInputElement
    input.value = 'abc'
    input.dispatchEvent(new Event('input'))
    await nextTick()

    const reason = host.querySelector('[data-testid="mp-confirm-reason"]') as HTMLElement | null
    expect(reason).toBeFalsy()

    app.unmount()
    host.remove()
  })

  it('AC-MP-7: trustlinesLoading=true shows (updating…) and fallback help', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'picking-payment-to',
      fromPid: 'alice' as string | null,
      toPid: null as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as any,
    })

    const app = createApp({
      render: () =>
        h(ManualPaymentPanel as any, {
          phase: 'picking-payment-to',
          state,

          unit: 'UAH',
          availableCapacity: null,

          trustlinesLoading: true,
          paymentTargetsLoading: false,
          paymentTargetsLastError: null,
          paymentToTargetIds: undefined,
          trustlines: [],

          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
          ],

          setFromPid: vi.fn(),
          setToPid: vi.fn(),

          busy: false,
          canSendPayment: true,
          confirmPayment: vi.fn(),
          cancel: vi.fn(),
          anchor: null,
          hostEl: null,
        }),
    })

    app.mount(host)
    await nextTick()

    const toLabel = Array.from(host.querySelectorAll('label')).find((l) => (l.textContent ?? '').includes('To'))
    expect(toLabel).toBeTruthy()
    expect((toLabel?.textContent ?? '')).toContain('(updating…')

    const help = host.querySelector('[data-testid="manual-payment-to-help"]') as HTMLElement
    expect((help.textContent ?? '').trim()).toContain('Routes are updating')

    app.unmount()
    host.remove()
  })

  it('AC-MP-8: known-empty paymentToTargetIds shows empty To list + help', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'picking-payment-to',
      fromPid: 'alice' as string | null,
      toPid: null as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as any,
    })

    const app = createApp({
      render: () =>
        h(ManualPaymentPanel as any, {
          phase: 'picking-payment-to',
          state,

          unit: 'UAH',
          availableCapacity: null,

          trustlinesLoading: false,
          paymentTargetsLoading: false,
          paymentTargetsLastError: null,
          paymentToTargetIds: new Set(),
          trustlines: [],

          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
          ],

          setFromPid: vi.fn(),
          setToPid: vi.fn(),

          busy: false,
          canSendPayment: false,
          confirmPayment: vi.fn(),
          cancel: vi.fn(),
          anchor: null,
          hostEl: null,
        }),
    })

    app.mount(host)
    await nextTick()

    const toSel = host.querySelector('#mp-to') as HTMLSelectElement
    const toOptionValues = Array.from(toSel.querySelectorAll('option')).map((o) => (o as HTMLOptionElement).value)
    expect(toOptionValues).toEqual([''])

    const help = host.querySelector('[data-testid="manual-payment-to-help"]') as HTMLElement
    expect((help.textContent ?? '').trim()).toContain('Backend reports no payment routes from selected sender.')

    app.unmount()
    host.remove()
  })

  it('MP-3: filters From dropdown by outgoing availability (direct-hop trustlines)', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'picking-payment-from',
      fromPid: null as string | null,
      toPid: null as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as any,
    })

    const app = createApp({
      render: () =>
        h(ManualPaymentPanel as any, {
          phase: 'picking-payment-from',
          state,

          unit: 'UAH',
          availableCapacity: null,

          trustlinesLoading: false,
          paymentTargetsLoading: false,
          paymentTargetsLastError: null,
          paymentToTargetIds: new Set(),
          trustlines: [
            // For payment A -> B, capacity is consumed on TL B -> A, so sender candidates are tl.to_pid.
            { from_pid: 'alice', to_pid: 'bob', available: '1', status: 'active' },
            { from_pid: 'alice', to_pid: 'carol', available: '0.01', status: 'active' },
            // No capacity: should not add.
            { from_pid: 'carol', to_pid: 'alice', available: '0', status: 'active' },
            // Inactive: should not add.
            { from_pid: 'alice', to_pid: 'dave', available: '10', status: 'inactive' },
          ],

          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
            { pid: 'carol', name: 'Carol' },
          ],

          setFromPid: vi.fn(),
          setToPid: vi.fn(),

          busy: false,
          canSendPayment: true,
          confirmPayment: vi.fn(),
          cancel: vi.fn(),

          anchor: null,
          hostEl: null,
        }),
    })

    app.mount(host)
    await nextTick()

    const fromSel = host.querySelector('#mp-from') as HTMLSelectElement
    const optionValues = Array.from(fromSel.querySelectorAll('option')).map((o) => (o as HTMLOptionElement).value)
    expect(optionValues).toEqual(['', 'bob', 'carol'])

    app.unmount()
    host.remove()
  })

  it.each([
    { name: 'undefined', trustlines: undefined as any },
    { name: '[]', trustlines: [] as any },
  ])('MP-3 fallback A: no trustlines ($name) => From dropdown shows full list', async ({ trustlines }) => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'picking-payment-from',
      fromPid: null as string | null,
      toPid: null as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as any,
    })

    const app = createApp({
      render: () =>
        h(ManualPaymentPanel as any, {
          phase: 'picking-payment-from',
          state,

          unit: 'UAH',
          availableCapacity: null,

          trustlinesLoading: false,
          paymentTargetsLoading: false,
          paymentTargetsLastError: null,
          paymentToTargetIds: new Set(),
          trustlines,

          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
            { pid: 'carol', name: 'Carol' },
          ],

          setFromPid: vi.fn(),
          setToPid: vi.fn(),

          busy: false,
          canSendPayment: true,
          confirmPayment: vi.fn(),
          cancel: vi.fn(),

          anchor: null,
          hostEl: null,
        }),
    })

    app.mount(host)
    await nextTick()

    const fromSel = host.querySelector('#mp-from') as HTMLSelectElement
    const optionValues = Array.from(fromSel.querySelectorAll('option')).map((o) => (o as HTMLOptionElement).value)
    expect(optionValues).toEqual(['', 'alice', 'bob', 'carol'])

    app.unmount()
    host.remove()
  })

  it('MP-3 fallback B: trustlines present but no outgoing capacity => From dropdown shows full list', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'picking-payment-from',
      fromPid: null as string | null,
      toPid: null as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as any,
    })

    const app = createApp({
      render: () =>
        h(ManualPaymentPanel as any, {
          phase: 'picking-payment-from',
          state,

          unit: 'UAH',
          availableCapacity: null,

          trustlinesLoading: false,
          paymentTargetsLoading: false,
          paymentTargetsLastError: null,
          paymentToTargetIds: new Set(),
          trustlines: [
            { from_pid: 'alice', to_pid: 'bob', available: '0', status: 'active' },
            { from_pid: 'alice', to_pid: 'carol', available: 'NaN', status: 'active' },
            { from_pid: 'alice', to_pid: 'dave', available: '', status: 'active' },
          ],

          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
            { pid: 'carol', name: 'Carol' },
          ],

          setFromPid: vi.fn(),
          setToPid: vi.fn(),

          busy: false,
          canSendPayment: true,
          confirmPayment: vi.fn(),
          cancel: vi.fn(),

          anchor: null,
          hostEl: null,
        }),
    })

    app.mount(host)
    await nextTick()

    const fromSel = host.querySelector('#mp-from') as HTMLSelectElement
    const optionValues = Array.from(fromSel.querySelectorAll('option')).map((o) => (o as HTMLOptionElement).value)
    expect(optionValues).toEqual(['', 'alice', 'bob', 'carol'])

    app.unmount()
    host.remove()
  })
})

