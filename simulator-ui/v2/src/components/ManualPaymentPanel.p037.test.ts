/**
 * Programme 037, `T3701` - reproducers for F-037-1 and F-037-3 on the CURRENT `ManualPaymentPanel`.
 *
 * Both are written against the panel that exists on `main`, not against the future wizard, so they are red
 * for a reason that can be read in the product code (`specs/037-manual-payment-mobile-prototype/spec.md`,
 * "Verification plan"). A test red only because "the wizard does not exist yet" would prove nothing.
 *
 * Controls that are green today are kept next to each reproducer (AGENTS.md 9, anti-vacuum): the number
 * IS shown when the server answered, and the comparison still works for pairs that `Number` does not collapse.
 *
 * Scope of what these do NOT see: the wizard's own "how much" step, the `payment-targets` source state
 * (`not asked / received / not received / null`) and the `routes[]` chain - none of those exist yet.
 */
import { computed, createApp, h, nextTick, reactive, ref, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ManualPaymentPanel from './ManualPaymentPanel.vue'
import { resetEquivalentPrecisions, setEquivalentPrecisions } from '../config/equivalentPrecision'
import { useInteractMode } from '../composables/useInteractMode'
import { subMoney } from '../utils/money'
import type { GraphSnapshot } from '../types'
import type { TrustlineInfo } from '../api/simulatorTypes'

const panel: Component = ManualPaymentPanel
type InteractActions = Parameters<typeof useInteractMode>[0]['actions']

afterEach(() => {
  resetEquivalentPrecisions()
  document.body.innerHTML = ''
})

async function settle() {
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
  await nextTick()
  await nextTick()
}

function actions(o: { fetchTrustlines: InteractActions['fetchTrustlines'] }): InteractActions {
  return {
    actionsDisabled: ref(false),
    sendPayment: vi.fn(async () => {
      throw new Error('not used')
    }),
    createTrustline: vi.fn(async () => {
      throw new Error('not used')
    }),
    updateTrustline: vi.fn(async () => {
      throw new Error('not used')
    }),
    closeTrustline: vi.fn(async () => {
      throw new Error('not used')
    }),
    runClearing: vi.fn(async () => {
      throw new Error('not used')
    }),
    fetchParticipants: vi.fn(async () => [
      { pid: 'alice', name: 'Alice', type: 'person', status: 'active' },
      { pid: 'bob', name: 'Bob', type: 'person', status: 'active' },
    ]),
    fetchTrustlines: o.fetchTrustlines,
    fetchPaymentTargets: vi.fn(async () => [{ to_pid: 'bob', hops: 1 }]),
  } as InteractActions
}

/** bob trusts alice up to 50, 5 used: payment alice -> bob has 45 of direct room - a number that exists ONLY in the snapshot. */
const SNAPSHOT_ONLY_FIGURE = '45.00'
function snapshotWithBobToAlice(): GraphSnapshot {
  return {
    equivalent: 'UAH',
    generated_at: '2026-10-08T00:00:00Z',
    nodes: [
      { id: 'alice', name: 'Alice', type: 'person', status: 'active' },
      { id: 'bob', name: 'Bob', type: 'person', status: 'active' },
    ],
    links: [
      {
        source: 'bob',
        target: 'alice',
        trust_limit: '50.00',
        used: '5.00',
        available: SNAPSHOT_ONLY_FIGURE,
        status: 'active',
      },
    ],
  }
}

async function confirmPhasePanel(fetchTrustlines: InteractActions['fetchTrustlines']) {
  window.history.replaceState({}, '', '/?mode=real&ui=interact')
  const snapshot = ref<GraphSnapshot | null>(snapshotWithBobToAlice())
  const im = useInteractMode({
    actions: actions({ fetchTrustlines }),
    runId: computed(() => 'run_p037'),
    equivalent: computed(() => 'UAH'),
    snapshot,
  })
  im.startPaymentFlow()
  im.setPaymentFromPid('alice')
  await settle()
  im.setPaymentToPid('bob')
  await settle()
  expect(im.phase.value, 'precondition: the flow must reach the confirm step').toBe('confirm-payment')

  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({
    render: () =>
      h(panel, {
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
      }),
  })
  app.mount(host)
  await settle()
  return { host, app, im }
}

/**
 * The figure is on screen AND nothing on screen says where it came from. "Says where it came from" is
 * deliberately loose here (the wizard's markup is not designed yet): an element carrying
 * `data-figures-source` whose value is anything but `server`. Hiding the figure satisfies the target too.
 */
function figureShownWithoutSource(host: HTMLElement): { shown: boolean; qualified: boolean } {
  const text = host.textContent ?? ''
  const shown = text.includes('45')
  const qualified = host.querySelector('[data-figures-source]:not([data-figures-source="server"])') !== null
  return { shown, qualified }
}

describe('F-037-1: the capacity hint of the current panel does not say where the number came from', () => {
  it('REPRODUCER (red now): REST trustlines never answered -> snapshot figure is shown as plain "Direct capacity"', async () => {
    const never = new Promise<TrustlineInfo[]>(() => {})
    const { host, app, im } = await confirmPhasePanel(vi.fn(async () => await never))
    try {
      // Precondition that makes this the "no REST answer" case and not the answered one.
      expect(im.trustlinesFetchState.value.kind, 'precondition: REST answer must be absent').not.toBe('answered')
      const { shown, qualified } = figureShownWithoutSource(host)
      expect(
        !shown || qualified,
        'F-037-1: the panel shows the snapshot-only figure "45" as "Direct capacity" with no mark of its source '
          + `while trustlines state is ${im.trustlinesFetchState.value.kind}; panel text: ${host.textContent?.replace(/\s+/g, ' ').trim()}`,
      ).toBe(true)
    } finally {
      app.unmount()
    }
  })

  it('REPRODUCER (red now): REST trustlines failed -> the same unqualified snapshot figure', async () => {
    const { host, app, im } = await confirmPhasePanel(
      vi.fn(async () => {
        throw new Error('HTTP 503')
      }),
    )
    try {
      expect(im.trustlinesFetchState.value.kind, 'precondition: REST answer must be absent').toBe('failed')
      const { shown, qualified } = figureShownWithoutSource(host)
      expect(
        !shown || qualified,
        'F-037-1: the panel shows the snapshot-only figure "45" after a FAILED trustlines fetch with no mark of its source',
      ).toBe(true)
    } finally {
      app.unmount()
    }
  })

  it('CONTROL (green now): when the server answered, the number is shown', async () => {
    const answered: TrustlineInfo[] = [
      {
        from_pid: 'bob',
        from_name: 'Bob',
        to_pid: 'alice',
        to_name: 'Alice',
        equivalent: 'UAH',
        limit: '50.00',
        used: '5.00',
        available: '45.00',
        status: 'active',
      } as TrustlineInfo,
    ]
    const { host, app, im } = await confirmPhasePanel(vi.fn(async () => answered))
    try {
      expect(im.trustlinesFetchState.value.kind, 'precondition: this is the answered case').toBe('answered')
      expect(host.textContent ?? '').toContain('45')
    } finally {
      app.unmount()
    }
  })
})

describe('F-037-3: the "exceeds capacity" warning compares money through Number()', () => {
  const UNIT = 'P8'

  async function warningFor(amount: string, available: string): Promise<boolean> {
    setEquivalentPrecisions([{ code: UNIT, precision: 8 }])
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
        h(panel, {
          phase: 'confirm-payment',
          state,
          unit: UNIT,
          availableCapacity: available,
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
    const input = host.querySelector('#mp-amount') as HTMLInputElement
    input.value = amount
    input.dispatchEvent(new Event('input'))
    await nextTick()
    await nextTick()
    const warning = host.querySelector('[data-testid="mp-confirm-warning"]')
    const reason = host.querySelector('[data-testid="mp-confirm-reason"]')
    app.unmount()
    host.remove()
    // The warning slot is empty because of a DISABLED reason, not because of equality: fail loudly then.
    expect(reason, `precondition: no disabled-reason expected for amount=${amount}`).toBeNull()
    return warning !== null
  }

  function exceedsByExactArithmetic(amount: string, available: string): boolean {
    const diff = subMoney(amount, available)
    return diff !== null && !diff.startsWith('-') && /[1-9]/.test(diff)
  }

  it('REPRODUCER (red now): amount one atom above capacity collapses to "not exceeding" in double', async () => {
    const amount = '999999999999.99999999'
    const available = '999999999999.99999998'
    expect(exceedsByExactArithmetic(amount, available), 'oracle: exact arithmetic says it exceeds').toBe(true)
    expect(
      await warningFor(amount, available),
      `exceedsCapacity("${amount}", "${available}") is false because Number() maps both to the same double`,
    ).toBe(true)
  })

  it('CONTROL (green now): the reverse pair and the equal pair do not warn', async () => {
    expect(await warningFor('999999999999.99999998', '999999999999.99999999')).toBe(false)
    expect(await warningFor('999999999999.99999999', '999999999999.99999999')).toBe(false)
  })

  it('CONTROL (green now): a pair that Number does not collapse still warns (the rule is not dead)', async () => {
    expect(await warningFor('11', '10')).toBe(true)
    expect(await warningFor('10', '11')).toBe(false)
  })

  it('CONTROL (green now): over every pair of non-negative values in money-rendering-conformance.json the panel agrees with subMoney', async () => {
    // Read from the repository like money.conformance.test.ts does - a copy would defeat the point.
    const { readFileSync } = await import('node:fs')
    const { resolve } = await import('node:path')
    const table = JSON.parse(
      readFileSync(resolve(__dirname, '../../../../api/money-rendering-conformance.json'), 'utf8'),
    ) as { cases: Array<{ value: string }> }
    const values = Array.from(new Set(table.cases.map((c) => c.value))).filter((v) => /^\d+(?:\.\d{1,8})?$/.test(v) && /[1-9]/.test(v))
    expect(values.length, 'the table must still give us a population to compare over').toBeGreaterThan(5)

    const mismatches: string[] = []
    for (const a of values) {
      for (const c of values) {
        const got = await warningFor(a, c)
        const want = exceedsByExactArithmetic(a, c)
        if (got !== want) mismatches.push(`amount=${a} available=${c}: panel=${got} exact=${want}`)
      }
    }
    expect(mismatches, mismatches.slice(0, 5).join('\n')).toEqual([])
  })
})
