/**
 * A seed scenario namespaces its participant ids (`<scenario id>:<community pid>`). Where the UI PRINTS an id to a person it
 * shows the id without the namespace of the scene on screen; values, keys and API arguments keep the full id.
 *
 * 044 `T4405` (decision (iv), 2026-10-17): a run's participant id is an identity of the run (`<run id>:p<n>`), so a picker
 * shows the participant's NAME and no id; the namespace rule is for an id printed WITHOUT a name. Under the name provider
 * (`provideParticipantNameOf`, given by `SimulatorAppRoot` from the snapshot) every printed id is the participant's name.
 *
 * Owner of the rule: `utils/participantDisplayId.ts`. These tests judge the places that print: they mount the real component
 * under a provider that says which scenario is on screen, and read what a person reads (option text, popup title, button).
 */
import { createApp, defineComponent, h, nextTick, reactive, ref, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import EdgeDetailPopup from './EdgeDetailPopup.vue'
import ManualPaymentPanel from './ManualPaymentPanel.vue'
import { provideParticipantNameOf, provideShownScenarioId } from '../composables/useParticipantDisplay'

const SCENARIO = 'greenfield-village-100-realistic-v2'
const OTHER = 'riverside-town-50-realistic-v2'
const ALICE = `${SCENARIO}:PID_U0046_6df7ddce`
const BOB = `${SCENARIO}:PID_U0047_aa11bb22`

afterEach(() => {
  document.body.innerHTML = ''
})

function mountUnder(
  scenarioId: string,
  child: Component,
  props: Record<string, unknown>,
  names?: Record<string, string>,
) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const scenario = ref(scenarioId)
  const Wrapper = defineComponent({
    setup() {
      provideShownScenarioId(scenario)
      if (names) provideParticipantNameOf((pid) => names[pid])
      return () => h(child, props)
    },
  })
  const app = createApp(Wrapper)
  app.mount(host)
  return { app, host }
}

function paymentPanelProps(o: { fromPid: string; participants: Array<{ pid: string; name: string }> }) {
  const state = reactive({
    phase: 'picking-payment-to',
    fromPid: o.fromPid as string | null,
    toPid: null as string | null,
    selectedEdgeKey: null as string | null,
    edgeAnchor: null as { x: number; y: number } | null,
    error: null as string | null,
    lastClearing: null,
  })
  return {
    phase: 'picking-payment-to',
    state,
    unit: 'UAH',
    availableCapacity: null,
    trustlinesLoading: false,
    paymentTargetsLoading: false,
    paymentTargetsLastError: null,
    paymentToTargetIds: new Set(o.participants.map((p) => p.pid)),
    trustlines: o.participants.flatMap((a) =>
      o.participants
        .filter((b) => b.pid !== a.pid)
        .map((b) => ({ status: 'active', available: '10', from_pid: a.pid, to_pid: b.pid })),
    ),
    participants: o.participants,
    setFromPid: vi.fn(),
    setToPid: vi.fn(),
    busy: false,
    canSendPayment: false,
    confirmPayment: vi.fn(),
    cancel: vi.fn(),
    anchor: null,
    hostEl: null,
  }
}

function optionsOf(host: HTMLElement, selector: string): Array<{ value: string; text: string }> {
  const sel = host.querySelector(selector) as HTMLSelectElement | null
  expect(sel).toBeTruthy()
  return Array.from((sel as HTMLSelectElement).querySelectorAll('option'))
    .map((o) => ({ value: (o as HTMLOptionElement).value, text: (o.textContent ?? '').trim() }))
    .filter((o) => o.value !== '')
}

describe('manual payment panel: From / To option labels', () => {
  const participants = [
    { pid: ALICE, name: 'Alex Turner (General Builder)' },
    { pid: BOB, name: 'Dana Lee' },
  ]

  it('prints the name and no id, keeps the full id as the option value (044: the id is not printed next to the name)', async () => {
    const { app, host } = mountUnder(SCENARIO, ManualPaymentPanel, paymentPanelProps({ fromPid: ALICE, participants }))
    await nextTick()
    await nextTick()

    const from = optionsOf(host, '#mp-from')
    expect(from.map((o) => o.value).sort()).toEqual([ALICE, BOB].sort())
    expect(from.find((o) => o.value === ALICE)?.text).toBe('Alex Turner (General Builder)')

    const to = optionsOf(host, '#mp-to')
    expect(to.map((o) => o.value)).toEqual([BOB])
    expect(to[0]!.text.startsWith('Dana Lee')).toBe(true)
    for (const o of [...from, ...to]) expect(o.text).not.toContain('PID_')

    for (const o of [...from, ...to]) expect(o.text).not.toContain(SCENARIO)
    app.unmount()
  })

  it("an id printed without a name: the namespace of the scene goes, ANOTHER scenario's stays (rule is exact)", async () => {
    const nameless = participants.map((p) => ({ ...p, name: '' }))
    const own = mountUnder(SCENARIO, ManualPaymentPanel, paymentPanelProps({ fromPid: ALICE, participants: nameless }))
    await nextTick()
    await nextTick()
    expect(optionsOf(own.host, '#mp-from').find((o) => o.value === ALICE)?.text).toBe('PID_U0046_6df7ddce')
    own.app.unmount()
    document.body.innerHTML = ''

    const { app, host } = mountUnder(OTHER, ManualPaymentPanel, paymentPanelProps({ fromPid: ALICE, participants: nameless }))
    await nextTick()
    await nextTick()

    const from = optionsOf(host, '#mp-from')
    expect(from.find((o) => o.value === ALICE)?.text).toBe(ALICE)
    app.unmount()
  })

  it('keeps two participants distinguishable', async () => {
    const twins = [
      { pid: ALICE, name: 'Same Name' },
      { pid: BOB, name: 'Same Name' },
    ]
    const { app, host } = mountUnder(SCENARIO, ManualPaymentPanel, paymentPanelProps({ fromPid: ALICE, participants: twins }))
    await nextTick()
    await nextTick()
    const texts = optionsOf(host, '#mp-from').map((o) => o.text)
    expect(new Set(texts).size).toBe(2)
    expect(texts.sort()).toEqual(['Same Name (1)', 'Same Name (2)']) // 044: told apart without printing the id
    app.unmount()
  })
})

describe('edge detail popup: title and Pay button', () => {
  function popupProps() {
    const state = reactive({
      phase: 'editing-trustline',
      fromPid: ALICE as string | null,
      toPid: BOB as string | null,
      selectedEdgeKey: `${ALICE}→${BOB}`,
      edgeAnchor: { x: 1, y: 2 },
      error: null,
      lastClearing: null,
    })
    return {
      phase: state.phase,
      state,
      unit: 'UAH',
      figuresSource: { kind: 'row' } as const,
      used: '0.00',
      limit: '10.00',
      available: '10.00',
      status: 'active',
      busy: false,
      close: () => undefined,
    }
  }

  it('prints both ids without the scenario namespace', async () => {
    const { app, host } = mountUnder(SCENARIO, EdgeDetailPopup, popupProps())
    await nextTick()
    expect(host.querySelector('.popup__subtitle')?.textContent?.trim()).toBe('PID_U0046_6df7ddce → PID_U0047_aa11bb22')
    const pay = host.querySelector('[data-testid="edge-send-payment"]') ?? Array.from(host.querySelectorAll('button')).find((b) => /Pay/.test(b.textContent ?? ''))
    expect(pay?.textContent?.trim()).toBe('Pay PID_U0046_6df7ddce')
    app.unmount()
  })

  it("044: under the name provider (a run on screen) prints the participants' names, never their ids", async () => {
    const RUN_A = 'run_20261017_101010_ab12cd34:p1'
    const RUN_B = 'run_20261017_101010_ab12cd34:p2'
    const props = popupProps()
    props.state.fromPid = RUN_A
    props.state.toPid = RUN_B
    const { app, host } = mountUnder(SCENARIO, EdgeDetailPopup, props, { [RUN_A]: 'Алиса', [RUN_B]: 'Магазин' })
    await nextTick()
    expect(host.querySelector('.popup__subtitle')?.textContent?.trim()).toBe('Алиса → Магазин')
    const pay = host.querySelector('[data-testid="edge-send-payment"]') ?? Array.from(host.querySelectorAll('button')).find((b) => /Pay/.test(b.textContent ?? ''))
    expect(pay?.textContent?.trim()).toBe('Pay Алиса')
    expect(host.textContent).not.toContain('run_')
    app.unmount()
  })

  it('shows the full ids when the scene belongs to another scenario', async () => {
    const { app, host } = mountUnder(OTHER, EdgeDetailPopup, popupProps())
    await nextTick()
    expect(host.querySelector('.popup__subtitle')?.textContent?.trim()).toBe(`${ALICE} → ${BOB}`)
    app.unmount()
  })
})
