import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { computed, createApp, h, nextTick, reactive, ref, type Component } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import ManualPaymentPanel from './ManualPaymentPanel.vue'
import { useInteractMode } from '../composables/useInteractMode'
import type { GraphSnapshot } from '../types'
import type {
  ParticipantInfo,
  SimulatorActionClearingRealResponse,
  SimulatorActionPaymentRealResponse,
  SimulatorActionTrustlineCloseResponse,
  SimulatorActionTrustlineCreateResponse,
  SimulatorActionTrustlineUpdateResponse,
  SimulatorPaymentTargetsItem,
  TrustlineInfo,
} from '../api/simulatorTypes'

type InteractActions = Parameters<typeof useInteractMode>[0]['actions']
type ManualPaymentPanelState = {
  phase: string
  fromPid: string | null
  toPid: string | null
  selectedEdgeKey: string | null
  edgeAnchor: { x: number; y: number } | null
  error: string | null
  lastClearing: null
}

const manualPaymentPanelComponent: Component = ManualPaymentPanel

function paymentSuccess(): SimulatorActionPaymentRealResponse {
  return {
    ok: true,
    payment_id: 'pay_1',
    from_pid: 'alice',
    to_pid: 'bob',
    amount: '1.00',
    equivalent: 'UAH',
    status: 'COMMITTED',
  }
}

function trustlineCreateSuccess(): SimulatorActionTrustlineCreateResponse {
  return {
    ok: true,
    trustline_id: 'tl_create_1',
    from_pid: 'alice',
    to_pid: 'bob',
    equivalent: 'UAH',
    limit: '10.00',
  }
}

function trustlineUpdateSuccess(): SimulatorActionTrustlineUpdateResponse {
  return {
    ok: true,
    trustline_id: 'tl_update_1',
    old_limit: '10.00',
    new_limit: '10.00',
  }
}

function trustlineCloseSuccess(): SimulatorActionTrustlineCloseResponse {
  return {
    ok: true,
    trustline_id: 'tl_close_1',
  }
}

function clearingSuccess(): SimulatorActionClearingRealResponse {
  return {
    ok: true,
    equivalent: 'UAH',
    cleared_cycles: 0,
    total_cleared_amount: '0.00',
    cycles: [],
  }
}

function participant(pid: string, name: string): ParticipantInfo {
  return {
    pid,
    name,
    type: 'person',
    status: 'active',
  }
}

function paymentTarget(to_pid: string, hops = 1): SimulatorPaymentTargetsItem {
  return {
    to_pid,
    hops,
  }
}

function makePanelState(partial: Partial<ManualPaymentPanelState>): ManualPaymentPanelState {
  return reactive({
    phase: 'idle',
    fromPid: null,
    toPid: null,
    selectedEdgeKey: null,
    edgeAnchor: null,
    error: null,
    lastClearing: null,
    ...partial,
  }) as ManualPaymentPanelState
}

function makeInteractActions(o: {
  participants: ParticipantInfo[]
  paymentTargetsImpl: () => Promise<SimulatorPaymentTargetsItem[]>
}): InteractActions {
  return {
    actionsDisabled: ref(false),
    sendPayment: vi.fn<InteractActions['sendPayment']>(async () => paymentSuccess()),
    createTrustline: vi.fn<InteractActions['createTrustline']>(async () => trustlineCreateSuccess()),
    updateTrustline: vi.fn<InteractActions['updateTrustline']>(async () => trustlineUpdateSuccess()),
    closeTrustline: vi.fn<InteractActions['closeTrustline']>(async () => trustlineCloseSuccess()),
    runClearing: vi.fn<InteractActions['runClearing']>(async () => clearingSuccess()),
    fetchParticipants: vi.fn<InteractActions['fetchParticipants']>(async () => o.participants),
    fetchTrustlines: vi.fn<InteractActions['fetchTrustlines']>(async () => [] as TrustlineInfo[]),
    fetchPaymentTargets: vi.fn<InteractActions['fetchPaymentTargets']>(async (eq: string, fromPid: string, maxHops?: number) => {
      expect(String(eq)).toBe('UAH')
      expect(String(fromPid)).toBe('alice')
      expect(maxHops === 6 || maxHops === 8).toBe(true)
      return await o.paymentTargetsImpl()
    }),
  }
}

async function settleUi() {
  // Let async composables (cache refreshes) resolve.
  await Promise.resolve()
  await Promise.resolve()
  // Let Vue paint.
  await nextTick()
  await nextTick()
}

function setUrl(search: string) {
  // Use a same-origin relative URL to satisfy happy-dom History security checks.
  window.history.replaceState({}, '', search)
}

function valuesSet(sel: HTMLSelectElement): Set<string> {
  return new Set(
    Array.from(sel.querySelectorAll('option'))
      .map((o) => (o as HTMLOptionElement).value)
      .filter((v) => v !== ''),
  )
}

function setEq(a: Set<string> | undefined, b: Set<string>) {
  expect(a).toBeInstanceOf(Set)
  const arrA = Array.from(a ?? []).sort()
  const arrB = Array.from(b).sort()
  expect(arrA).toEqual(arrB)
}

describe('ManualPaymentPanel', () => {
  it('MP-3: fromParticipants always includes selected fromPid even if filtered out by outgoing capacity', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'picking-payment-to',
      fromPid: 'alice' as string | null,
      toPid: null as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null,
    })

    // Outgoing candidates are derived from tl.to_pid for active TLs with available > 0.
    // Provide a TL that makes only 'carol' eligible, so 'alice' would be filtered out
    // without the "always include selected" rule.
    const trustlines = [
      {
        status: 'active',
        available: '10',
        to_pid: 'carol',
      },
    ]

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
          phase: 'picking-payment-to',
          state,

          unit: 'UAH',
          availableCapacity: null,

          trustlinesLoading: false,
          paymentTargetsLoading: false,
          paymentTargetsLastError: null,
          paymentToTargetIds: undefined,
          trustlines,

          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'carol', name: 'Carol' },
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
    await nextTick()

    const fromSel = host.querySelector('#mp-from') as HTMLSelectElement | null
    expect(fromSel).toBeTruthy()

    const vals = valuesSet(fromSel as HTMLSelectElement)
    expect(vals.has('alice')).toBe(true)
    expect(vals.has('carol')).toBe(true)

    // Ensure the selected participant is near the top (after the placeholder option).
    const opts = Array.from((fromSel as HTMLSelectElement).querySelectorAll('option')).map((o) =>
      (o as HTMLOptionElement).value,
    )
    const firstReal = opts.find((v) => v !== '')
    expect(firstReal).toBe('alice')

    app.unmount()
    host.remove()
  })

  it('AC-A11Y-1/AC-A11Y-2: Amount aria-describedby=mp-amount-help + To aria-describedby=mp-to-help (help elements exist)', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'confirm-payment',
      fromPid: 'alice',
      toPid: 'bob',
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null,
    })

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
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

    const toLabel = host.querySelector('#mp-to-label') as HTMLLabelElement | null
    const toTrigger = host.querySelector('#mp-to__trigger') as HTMLButtonElement | null
    expect(toLabel?.htmlFor).toBe('mp-to__trigger')
    expect(toTrigger?.getAttribute('aria-labelledby')).toBe('mp-to-label')

    const amountInput = host.querySelector('#mp-amount') as HTMLInputElement
    expect(amountInput.getAttribute('aria-describedby')).toBe('mp-amount-help')
    const amountHelp = host.querySelector('#mp-amount-help') as HTMLElement | null
    expect(amountHelp).toBeTruthy()

    app.unmount()
    host.remove()
  })

  it('AC-MP-12: refresh removes toPid from availableTargetIds => resets selection + inline warning', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'picking-payment-to',
      fromPid: 'alice' as string | null,
      toPid: 'bob' as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null,
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
        h(manualPaymentPanelComponent, {
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

  it('closes the To dropdown on Escape without unmounting the payment panel', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'picking-payment-to',
      fromPid: 'alice' as string | null,
      toPid: 'bob' as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null,
    })

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
          phase: 'picking-payment-to',
          state,
          unit: 'UAH',
          availableCapacity: null,
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
        }),
    })

    app.mount(host)
    await nextTick()
    await nextTick()

    const trigger = host.querySelector('#mp-to__trigger') as HTMLButtonElement | null
    expect(trigger).toBeTruthy()

    trigger?.focus()
    trigger?.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true }))
    await nextTick()
    await nextTick()

    const dropdown = document.body.querySelector('#mp-to__surface') as HTMLElement | null
    expect(dropdown).toBeTruthy()

    const selected = dropdown?.querySelector('[data-dropdown-selected="1"]') as HTMLButtonElement | null
    selected?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    await nextTick()
    await nextTick()

    expect(document.body.querySelector('#mp-to__surface')).toBeNull()
    expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()

    app.unmount()
    host.remove()
  })

  it('AC-MP: selecting From as To clears To and shows self-payment warning', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'picking-payment-to',
      fromPid: 'alice' as string | null,
      toPid: 'alice' as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null,
    })

    const ui = reactive({
      phase: 'picking-payment-to' as const,
      paymentToTargetIds: undefined as Set<string> | undefined,
    })

    const setToPid = vi.fn((pid: string | null) => {
      state.toPid = pid
    })

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
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
          ],

          setFromPid: vi.fn(),
          setToPid,

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

    // Become known to trigger the watcher.
    ui.paymentToTargetIds = new Set(['bob'])
    await nextTick()
    await nextTick()

    expect(state.toPid).toBe(null)

    const warn = host.querySelector('[data-testid="manual-payment-to-invalid-warn"]') as HTMLElement
    expect(warn).toBeTruthy()
    expect((warn.textContent ?? '').trim()).toContain('You cannot send a payment to yourself')

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
      lastClearing: null,
    })

    const ui = reactive({
      phase: 'confirm-payment' as const,
      paymentToTargetIds: new Set(['bob']),
    })

    const confirmPayment = vi.fn()

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
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
      lastClearing: null,
    })

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
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
      lastClearing: null,
    })

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
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
      lastClearing: null,
    })

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
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

  it("MP-2: To option renders capacity '0' (does not treat '0' as unknown)", async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'picking-payment-to',
      fromPid: 'alice',
      toPid: null as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null,
    })

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
          phase: 'picking-payment-to',
          state,
          unit: 'UAH',
          availableCapacity: null,

          trustlinesLoading: false,
          paymentTargetsLoading: false,
          paymentTargetsLastError: null,
          paymentToTargetIds: new Set(['bob']),
          trustlines: [
            {
              // For payment alice -> bob, capacity is defined by trustline bob -> alice.
              from_pid: 'bob',
              from_name: 'Bob',
              to_pid: 'alice',
              to_name: 'Alice',
              equivalent: 'UAH',
              limit: '10.00',
              used: '10.00',
              available: '0',
              status: 'active',
            },
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
    expect((opt?.textContent ?? '').trim()).toContain('0 UAH')
    expect((opt?.textContent ?? '').trim()).not.toContain('…')

    app.unmount()
    host.remove()
  })

  it('AC-MP-5b: amount > direct capacity shows inline warning and does NOT disable confirm (multi-hop mode)', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'confirm-payment',
      fromPid: 'alice',
      toPid: 'bob',
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null,
    })

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
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
    await nextTick()

    // Phase 2.5 multi-hop: exceeding direct capacity is a non-blocking warning.
    const warn = host.querySelector('[data-testid="mp-confirm-warning"]') as HTMLElement
    expect(warn).toBeTruthy()
    expect((warn.textContent ?? '').trim()).toContain('Amount may exceed direct trustline capacity')
    expect((warn.textContent ?? '').trim()).toContain('10 UAH')

    // Warning is non-blocking: confirm stays enabled.
    const reason = host.querySelector('[data-testid="mp-confirm-reason"]') as HTMLElement | null
    expect(reason).toBeFalsy()

    const btn = host.querySelector('[data-testid="manual-payment-confirm"]') as HTMLButtonElement
    expect(btn.disabled).toBe(false)

    app.unmount()
    host.remove()
  })

  it('AC-MP-19: confirm step shows disabled reason when canSendPayment=false (no route between selected participants)', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'confirm-payment',
      fromPid: 'alice',
      toPid: 'bob',
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null,
    })

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
          phase: 'confirm-payment',
          state,
          unit: 'UAH',
          availableCapacity: '10',

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

    const input = host.querySelector('#mp-amount') as HTMLInputElement
    input.value = '1'
    input.dispatchEvent(new Event('input'))
    await nextTick()

    const reason = host.querySelector('[data-testid="mp-confirm-reason"]') as HTMLElement | null
    expect(reason).toBeTruthy()
    expect((reason?.textContent ?? '').trim()).toBe('Backend reports no payment routes between selected participants.')

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
      lastClearing: null,
    })

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
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
      lastClearing: null,
    })

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
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
      lastClearing: null,
    })

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
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

    // UX-10 (Phase 2): known-empty => disable To select (no actionable choices).
    expect(toSel.disabled).toBe(true)

    const help = host.querySelector('[data-testid="manual-payment-to-help"]') as HTMLElement
    expect((help.textContent ?? '').trim()).toContain(
      'Backend reports no payment routes from selected sender',
    )

    app.unmount()
    host.remove()
  })

  it('AC-MP-11: FROM filtered when trustlines have outgoing (available > 0) (direct-hop)', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'picking-payment-from',
      fromPid: null as string | null,
      toPid: null as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null,
    })

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
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

  it.each<{
    name: string
    trustlines: TrustlineInfo[] | undefined
  }>([
    { name: 'undefined', trustlines: undefined },
    { name: '[]', trustlines: [] },
  ])('AC-MP-11: fallback full FROM list when trustlines are empty ($name)', async ({ trustlines }) => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'picking-payment-from',
      fromPid: null as string | null,
      toPid: null as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null,
    })

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
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

  it('degraded mode: paymentTargetsLastError => To list shows fallback (not known-empty) + error help text', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'picking-payment-to',
      fromPid: 'alice' as string | null,
      toPid: null as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null,
    })

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
          phase: 'picking-payment-to',
          state,

          unit: 'UAH',
          availableCapacity: null,

          // Error on last fetch => degrade to unknown (show fallback list, not empty)
          trustlinesLoading: false,
          paymentTargetsLoading: false,
          paymentTargetsLastError: 'Connection timeout',
          paymentToTargetIds: new Set(['bob']),
          trustlines: [],

          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
            { pid: 'carol', name: 'Carol' },
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

    // When paymentTargetsLastError is set, dropdownToTargetIds becomes undefined (degraded).
    // To-list should NOT be known-empty; it should show the fallback participants.
    const toSel = host.querySelector('#mp-to') as HTMLSelectElement
    const toOptionValues = Array.from(toSel.querySelectorAll('option')).map((o) => (o as HTMLOptionElement).value)
    // Degraded mode: 'bob' and 'carol' visible (not empty), 'alice' excluded as fromPid.
    expect(toOptionValues).toContain('bob')
    expect(toOptionValues).toContain('carol')
    expect(toOptionValues).not.toContain('alice')

    // To select should NOT be disabled (not in known-empty state)
    expect(toSel.disabled).toBe(false)

    // Help text should indicate the routes update failed.
    const help = host.querySelector('[data-testid="manual-payment-to-help"]') as HTMLElement
    expect((help.textContent ?? '').trim()).toContain('Routes update failed')

    app.unmount()
    host.remove()
  })

  it('AC-MP-11: fallback full FROM list when trustlines exist but have no outgoing capacity', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'picking-payment-from',
      fromPid: null as string | null,
      toPid: null as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null,
    })

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
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

  describe('Phase 2.5 backend-first payment targets (AC-MP-15..18)', () => {
    function mountWithInteractMode(im: ReturnType<typeof useInteractMode>) {
      const host = document.createElement('div')
      document.body.appendChild(host)

      const app = createApp({
        render: () =>
          h(manualPaymentPanelComponent, {
            phase: im.phase.value,
            state: im.state,
            unit: 'UAH',
            availableCapacity: im.availableCapacity.value,

            trustlinesLoading: im.trustlinesLoading.value,
            paymentTargetsLoading: im.paymentTargetsLoading.value,
            paymentTargetsMaxHops: im.paymentTargetsMaxHops,
            paymentTargetsLastError: im.paymentTargetsLastError.value,
            paymentToTargetIds: im.paymentToTargetIds.value,
            trustlines: im.trustlines.value,
            trustlinesLastError: im.trustlinesLastError.value,
            participants: im.participants.value,

            busy: im.busy.value,
            canSendPayment: im.canSendPayment.value,

            confirmPayment: im.confirmPayment,
            cancel: im.cancel,

            setFromPid: im.setPaymentFromPid,
            setToPid: im.setPaymentToPid,

            anchor: null,
            hostEl: null,
          }),
      })

      app.mount(host)
      return { app, host }
    }

    it('AC-MP-15: To dropdown contains exactly payment-targets.items[].to_pid (known-nonempty)', async () => {
      setUrl('/?mode=real&ui=interact')

      const participants = [
        participant('alice', 'Alice'),
        participant('bob', 'Bob'),
        participant('carol', 'Carol'),
        participant('dave', 'Dave'),
      ] satisfies ParticipantInfo[]
      const actions = makeInteractActions({
        participants,
        paymentTargetsImpl: async () => [paymentTarget('bob'), paymentTarget('carol')],
      })

      const snapshot = ref<GraphSnapshot | null>(null)
      const im = useInteractMode({
        actions,
        runId: computed(() => 'run_test'),
        equivalent: computed(() => 'UAH'),
        snapshot,
      })

      im.startPaymentFlow()
      im.setPaymentFromPid('alice')
      await settleUi()

      const { app, host } = mountWithInteractMode(im)
      try {
        await settleUi()

        const toSel = host.querySelector('#mp-to') as HTMLSelectElement
        expect(toSel).toBeTruthy()

        expect(valuesSet(toSel)).toEqual(new Set(['bob', 'carol']))
      } finally {
        app.unmount()
        host.remove()
      }
    })

    it('AC-MP-16: availableTargetIds for canvas equals payment-targets.items[].to_pid (known-nonempty)', async () => {
      setUrl('/?mode=real&ui=interact')

      const participants = [
        participant('alice', 'Alice'),
        participant('bob', 'Bob'),
        participant('carol', 'Carol'),
      ] satisfies ParticipantInfo[]
      const expected = new Set(['bob', 'carol'])
      const actions = makeInteractActions({
        participants,
        paymentTargetsImpl: async () => [paymentTarget('bob'), paymentTarget('carol')],
      })

      const snapshot = ref<GraphSnapshot | null>(null)
      const im = useInteractMode({
        actions,
        runId: computed(() => 'run_test'),
        equivalent: computed(() => 'UAH'),
        snapshot,
      })

      im.startPaymentFlow()
      im.setPaymentFromPid('alice')
      await settleUi()

      // Payment targets are backend-first; once known, highlight set must match.
      setEq(im.paymentToTargetIds.value, expected)
      setEq(im.availableTargetIds.value, expected)
    })

    it('AC-MP-17: backend-first known-empty shows "Backend reports no payment routes from selected sender" (and includes max_hops in copy)', async () => {
      // Explicitly test max_hops gating copy branch (8 deep).
      setUrl('/?mode=real&ui=interact&payMaxHops=8')

      const participants = [
        participant('alice', 'Alice'),
        participant('bob', 'Bob'),
      ] satisfies ParticipantInfo[]
      const actions = makeInteractActions({
        participants,
        paymentTargetsImpl: async () => [],
      })

      const snapshot = ref<GraphSnapshot | null>(null)
      const im = useInteractMode({
        actions,
        runId: computed(() => 'run_test'),
        equivalent: computed(() => 'UAH'),
        snapshot,
      })

      im.startPaymentFlow()
      im.setPaymentFromPid('alice')
      await settleUi()

      const { app, host } = mountWithInteractMode(im)
      try {
        await settleUi()

        const toSel = host.querySelector('#mp-to') as HTMLSelectElement
        expect(toSel.disabled).toBe(true)

        const help = host.querySelector('[data-testid="manual-payment-to-help"]') as HTMLElement
        const txt = (help.textContent ?? '').trim()
        expect(txt).toContain('Backend reports no payment routes from selected sender')
        expect(txt).toContain('max hops: 8')
      } finally {
        app.unmount()
        host.remove()
      }
    })

    it('AC-MP-18: payment-targets request runs once on From selection; not repeated on amount changes / rerender', async () => {
      setUrl('/?mode=real&ui=interact')

      const participants = [
        participant('alice', 'Alice'),
        participant('bob', 'Bob'),
      ] satisfies ParticipantInfo[]
      const actions = makeInteractActions({
        participants,
        paymentTargetsImpl: async () => [paymentTarget('bob')],
      })

      const snapshot = ref<GraphSnapshot | null>(null)
      const im = useInteractMode({
        actions,
        runId: computed(() => 'run_test'),
        equivalent: computed(() => 'UAH'),
        snapshot,
      })

      im.startPaymentFlow()
      im.setPaymentFromPid('alice')
      await settleUi()
      expect(actions.fetchPaymentTargets).toHaveBeenCalledTimes(1)

      // Move to confirm-payment so amount input exists.
      im.setPaymentToPid('bob')
      await settleUi()
      expect(actions.fetchPaymentTargets).toHaveBeenCalledTimes(1)

      const { app, host } = mountWithInteractMode(im)
      try {
        await settleUi()

        const input = host.querySelector('#mp-amount') as HTMLInputElement
        expect(input).toBeTruthy()

        for (const v of ['1', '2', '3.5']) {
          input.value = v
          input.dispatchEvent(new Event('input'))
          await nextTick()
        }

        expect(actions.fetchPaymentTargets).toHaveBeenCalledTimes(1)
      } finally {
        app.unmount()
        host.remove()
      }
    })

    it('backend-first: paymentTargetsLoading=true (unknown) shows updating help + fallback To list until resolved', async () => {
      setUrl('/?mode=real&ui=interact')

      const participants = [
        participant('alice', 'Alice'),
        participant('bob', 'Bob'),
        participant('carol', 'Carol'),
        participant('dave', 'Dave'),
      ] satisfies ParticipantInfo[]

      let resolveTargets!: (v: SimulatorPaymentTargetsItem[]) => void
      const paymentTargetsImpl = () =>
        new Promise<SimulatorPaymentTargetsItem[]>((res) => {
          resolveTargets = res
        })

      const actions = makeInteractActions({ participants, paymentTargetsImpl })

      const snapshot = ref<GraphSnapshot | null>(null)
      const im = useInteractMode({
        actions,
        runId: computed(() => 'run_test'),
        equivalent: computed(() => 'UAH'),
        snapshot,
      })

      im.startPaymentFlow()
      im.setPaymentFromPid('alice')

      const { app, host } = mountWithInteractMode(im)
      try {
        await settleUi()

        const help = host.querySelector('[data-testid="manual-payment-to-help"]') as HTMLElement
        expect((help.textContent ?? '').trim()).toContain('Routes are updating')

        const toSel = host.querySelector('#mp-to') as HTMLSelectElement
        // Unknown => fallback list (all except fromPid), thus includes dave.
        expect(valuesSet(toSel).has('dave')).toBe(true)

        // Now resolve backend payment-targets to known set.
        resolveTargets([paymentTarget('bob')])
        await settleUi()

        // Known => filtered to exactly returned target(s).
        expect(valuesSet(toSel)).toEqual(new Set(['bob']))
      } finally {
        app.unmount()
        host.remove()
      }
    })

    it('backend-first degraded: payment-targets error shows fallback To list + degraded help; highlight targets become unknown', async () => {
      setUrl('/?mode=real&ui=interact')

      const participants = [
        participant('alice', 'Alice'),
        participant('bob', 'Bob'),
        participant('carol', 'Carol'),
      ] satisfies ParticipantInfo[]
      const actions = makeInteractActions({
        participants,
        paymentTargetsImpl: async () => {
          throw new Error('boom')
        },
      })

      const snapshot = ref<GraphSnapshot | null>(null)
      const im = useInteractMode({
        actions,
        runId: computed(() => 'run_test'),
        equivalent: computed(() => 'UAH'),
        snapshot,
      })

      im.startPaymentFlow()
      im.setPaymentFromPid('alice')
      await settleUi()

      // When backend targets refresh failed, the canvas highlight must degrade to unknown.
      expect(im.paymentTargetsLastError.value).toBeTruthy()
      expect(im.availableTargetIds.value).toBeUndefined()

      const { app, host } = mountWithInteractMode(im)
      try {
        await settleUi()

        const toSel = host.querySelector('#mp-to') as HTMLSelectElement
        // Degraded => fallback list (not known-empty).
        expect(valuesSet(toSel)).toEqual(new Set(['bob', 'carol']))

        const help = host.querySelector('[data-testid="manual-payment-to-help"]') as HTMLElement
        expect((help.textContent ?? '').trim()).toContain('Routes update failed')
      } finally {
        app.unmount()
        host.remove()
      }
    })

    it('Batch 2a: long participant labels keep the consumer on shared ds-controls markup without local width hacks', async () => {
      const host = document.createElement('div')
      document.body.appendChild(host)

      const state = reactive({
        phase: 'picking-payment-to',
        fromPid: 'alice' as string | null,
        toPid: null as string | null,
        selectedEdgeKey: null as string | null,
        edgeAnchor: null as { x: number; y: number } | null,
        error: null as string | null,
        lastClearing: null,
      })

      const longName = 'Long option label '.repeat(12).trim()

      const app = createApp({
        render: () =>
          h(manualPaymentPanelComponent, {
            phase: 'picking-payment-to',
            state,
            unit: 'UAH',
            availableCapacity: null,
            trustlinesLoading: false,
            paymentTargetsLoading: false,
            paymentTargetsLastError: null,
            paymentToTargetIds: new Set(['bob']),
            trustlines: [],
            participants: [
              { pid: 'alice', name: longName },
              { pid: 'bob', name: longName },
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

      const panel = host.querySelector('[data-testid="manual-payment-panel"]') as HTMLElement | null
      const row = host.querySelector('#mp-to')?.closest('.ds-controls__row') as HTMLElement | null
      const select = host.querySelector('#mp-to') as HTMLSelectElement | null

      expect(panel).toBeTruthy()
      expect(row).toBeTruthy()
      expect(select).toBeTruthy()

      expect(panel!.style.width).toBe('')
      expect(panel!.style.maxWidth).toBe('')
      expect(row!.classList.contains('ds-controls__row')).toBe(true)
      expect((select!.querySelector('option[value="bob"]')?.textContent ?? '')).toContain('Long option label')

      app.unmount()
      host.remove()
    })

    it('Batch 2b: uses shared compact form primitives instead of local width/stretch hacks', () => {
      const source = readFileSync(resolve(process.cwd(), 'src/components/ManualPaymentPanel.vue'), 'utf8')

      expect(source).toContain('ds-ov-panel ds-ov-panel--compact ds-panel ds-panel--elevated')
      expect(source).toContain('class="ds-controls__row ds-controls__row--compact"')
      expect(source).toContain('class="ds-controls__suffix mp-amount-row"')
      expect(source).not.toContain('mp-amount-input {')
    })
  })
})






