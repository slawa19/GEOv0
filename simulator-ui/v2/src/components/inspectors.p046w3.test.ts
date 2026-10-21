/**
 * 046 wave 3 (T4604): the inspectors - the participant card (`NodeCardOverlay.vue`) and the line window
 * (`EdgeDetailPopup.vue`) - and the texts the actions leave behind (the history, the success notification, a window's
 * failure) speak ONE language, the language of NOW.
 *
 * The rule is structural, as in wave 1 (`firstScreen.p046w1.test.ts`, AGENTS.md §9: no list of words to look for):
 * everything the mounted surface SAYS - visible text and the `aria-label` / `title` / `placeholder` / `aria-valuetext` a
 * keyboard or screen-reader user meets - is read together; under `ru` no Latin word except data, under `en` no Cyrillic
 * except the names given as data (by value, not by pattern). Data allowed under `ru`: the equivalent code and the
 * participant ids the line window prints in its title (no names are provided to it here).
 *
 * Red before the wave on `main` `d195e259`: both inspectors were English as a whole («Pay», «New line», «Edit trustline»,
 * «Close line», «Used / Limit · avail»...), the card said the roles in its own words («Even overall» beside the legend's
 * «even»), the line window had no explanatory line (048 item 13 (б)), the history and the success notification kept the
 * language they were made in, a window's failure too. (The busy cancel's native confirm, Russian in English before the
 * wave, is gone - Escape and an outside click while busy are ignored: `SimulatorAppRoot.interact.test.ts` P0-2 and
 * `useSimulatorApp.windowManagementStep0.test.ts`.)
 *
 * What this does NOT see: the width of the Russian texts at 1366x768 (the browser test `e2e/p046-w3-inspectors.spec.ts`
 * measures it); whether a translation reads well (the parity guard and a human).
 */
import { computed, createApp, h, nextTick, reactive, ref, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useInteractMode } from '../composables/useInteractMode'
import { useInteractActions } from '../composables/useInteractActions'
import { freezeTrustlineFiguresSource, resolveTrustlineFiguresSource, trustlineFiguresNotice } from '../composables/interact/trustlinesSourceState'
import { setUiLocale, type UiLocale } from '../i18n/locale'
import { t } from '../i18n/t'
import type { GraphNode, GraphSnapshot } from '../types'
import { resolveWindowSurfaceDescriptor } from '../ui-kit/overlaySurfaceCatalog'
import type { WindowInstance } from '../composables/windowManager/types'
import { DATA_PALETTE_DEFAULT } from '../vizMapping'
import EdgeDetailPopup from './EdgeDetailPopup.vue'
import InteractHistoryLog from './InteractHistoryLog.vue'
import Legend from './Legend.vue'
import NodeCardOverlay from './NodeCardOverlay.vue'
import ClearingPanel from './ClearingPanel.vue'
import ManualPaymentPanel from './ManualPaymentPanel.vue'
import { useInteractFSM } from '../composables/interact/useInteractFSM'

/** Names are data: Cyrillic in both runs, excluded from the `en` check by value. */
const NAMES = new Set(['Алиса', 'Борис', 'Вера', 'Узел'])
/**
 * Data under `ru`: the equivalent code, the participant ids the line window prints (`showPid` without names), the request
 * id's prefix (`req-1234`) and the key `Esc` (the parity guard's allow-list).
 */
const RU_DATA_WORDS = new Set(['UAH', 'alice', 'bob', 'req', 'Esc'])

afterEach(() => {
  setUiLocale('en')
  window.localStorage.clear()
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

async function settle() {
  for (let i = 0; i < 5; i += 1) await nextTick()
}

function said(root: ParentNode = document.body): string {
  const parts: string[] = []
  const walker = document.createTreeWalker(root as Node, NodeFilter.SHOW_TEXT)
  for (let n = walker.nextNode(); n; n = walker.nextNode()) parts.push(n.nodeValue ?? '')
  for (const el of Array.from(root.querySelectorAll('*'))) {
    for (const a of ['aria-label', 'title', 'placeholder', 'aria-valuetext']) {
      const v = el.getAttribute(a)
      if (v) parts.push(v)
    }
  }
  return parts.join(' \n ').replace(/\s+/g, ' ')
}

function foreignWords(locale: UiLocale, root: ParentNode = document.body): string[] {
  const text = said(root)
  if (locale === 'ru') return (text.match(/[A-Za-z_]+/g) ?? []).filter((w) => !RU_DATA_WORDS.has(w))
  return (text.match(/[Ѐ-ӿ]+/g) ?? []).filter((w) => !NAMES.has(w))
}

function mount(component: Component, props: Record<string, unknown>) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ render: () => h(component, props) })
  app.mount(host)
  return { host, app }
}

const line = (from: string, fromName: string, to: string, toName: string, over: Record<string, unknown> = {}) => ({
  from_pid: from, from_name: fromName, to_pid: to, to_name: toName, equivalent: 'UAH',
  limit: '100.00', used: '12.00', available: '88.00', status: 'active', ...over,
})

const cardProps = (over: Record<string, unknown> = {}) => ({
  node: { id: 'alice', name: 'Алиса', status: 'active', type: 'person', net_sign: -1, net_balance: '-12.00' } as GraphNode,
  edgeStats: { outLimitText: '100.00', inLimitText: '50.00' },
  equivalentText: 'UAH',
  palette: DATA_PALETTE_DEFAULT,
  showPinActions: true, isPinned: false, pin: vi.fn(), unpin: vi.fn(),
  interactMode: true,
  interactTrustlines: [
    line('alice', 'Алиса', 'bob', 'Борис'),
    line('alice', 'Алиса', 'vera', 'Вера', { used: '105.00', available: '-5.00' }),
    line('bob', 'Борис', 'alice', 'Алиса'),
  ],
  trustlinesLoading: false, interactBusy: false, interactPhase: 'idle',
  onInteractSendPayment: vi.fn(), onInteractNewTrustline: vi.fn(), onInteractEditTrustline: vi.fn(),
  ...over,
})

function popupProps(over: Record<string, unknown> = {}) {
  const state = reactive({
    phase: 'editing-trustline', fromPid: 'alice', toPid: 'bob', selectedEdgeKey: 'alice→bob', edgeAnchor: { x: 1, y: 1 },
    error: null, lastClearing: null,
  })
  return {
    phase: 'editing-trustline', state, unit: 'UAH', figuresSource: { kind: 'row' }, used: '12.00', limit: '100.00',
    available: '88.00', status: 'active', busy: false, close: vi.fn(), ...over,
  }
}

/** Every state of the two inspectors this file knows, at once. */
async function mountInspectors(): Promise<void> {
  mount(NodeCardOverlay, cardProps({ isPinned: true }))
  mount(NodeCardOverlay, cardProps({ isPinned: false, interactBusy: true }))
  mount(NodeCardOverlay, cardProps({ interactPhase: 'picking-payment-to' }))
  mount(NodeCardOverlay, cardProps({ interactTrustlines: [], trustlinesLoading: true }))
  mount(NodeCardOverlay, cardProps({ interactTrustlines: [] }))
  // A frozen organisation that is owed more, a hub, and a participant whose balance is unknown.
  mount(NodeCardOverlay, cardProps({ node: { id: 'bob', name: 'Борис', status: 'frozen', type: 'business', net_sign: 1, viz_shape_key: 'rounded-rect' } }))
  mount(NodeCardOverlay, cardProps({ node: { id: 'hub', name: 'Узел', status: 'active', type: 'hub', net_sign: 0 } }))
  mount(NodeCardOverlay, cardProps({ node: { id: 'vera', name: 'Вера', status: 'suspended', type: 'person', net_sign: null } }))

  mount(EdgeDetailPopup, popupProps())
  mount(EdgeDetailPopup, popupProps({ used: '0.00', available: '100.00', status: 'closed' }))
  mount(EdgeDetailPopup, popupProps({ closeRequestedAt: '2026-10-19T08:00:00Z', limit: '0.00', available: '-12.00' }))
  for (const figuresSource of [
    { kind: 'no-row' }, { kind: 'frozen' }, { kind: 'never-asked' }, { kind: 'loading' }, { kind: 'loading', frozen: true },
    // As the data cache makes it (046 wave 3): the failure's own sentence in any language (`render`).
    { kind: 'failed', message: t('actionError.NETWORK'), render: (l: UiLocale) => t('actionError.NETWORK', {}, l), ref: 'req-1234' },
  ]) mount(EdgeDetailPopup, popupProps({ figuresSource }))
  // The close armed: "Confirm close" and "Cancel".
  const armed = mount(EdgeDetailPopup, popupProps({ used: '0.00' }))
  await settle()
  armed.host.querySelector<HTMLButtonElement>('[data-testid="edge-close-line-btn"]')!.click()
  await settle()
}

describe('046 wave 3 - the inspectors in one language', () => {
  it('counter-check: the states are mounted - each surface named here is in the DOM (a check over an empty page would pass)', async () => {
    await mountInspectors()
    for (const id of [
      'node-card-name', 'node-card-role', 'node-card-meta', 'node-card-stats', 'node-card-send-payment', 'node-card-new-trustline',
      'node-card-actions-reason', 'node-card-line-available', 'edge-detail-popup', 'edge-stats', 'edge-status', 'edge-source-unavailable',
      'edge-no-trustline', 'edge-frozen-figures', 'edge-close-requested', 'edge-close-request-note',
      'edge-payment-direction', 'edge-send-payment', 'edge-change-limit', 'edge-close-line-btn', 'edge-close-line-cancel',
    ]) expect(document.querySelector(`[data-testid="${id}"]`), id).not.toBeNull()
    expect(document.querySelectorAll('.nco-trustline-row').length, 'rows of both groups').toBeGreaterThan(3)
    // 049 S2 (decision 7): the failed read's request id is not drawn; it stays the warning's `data-request-id` (section 12).
    expect([...document.querySelectorAll('[data-testid="edge-source-unavailable"]')].map((e) => e.getAttribute('data-request-id'))).toContain('req-1234')
    expect(document.querySelector('[data-testid="error-request-id"]'), 'no drawn request-id line').toBeNull()
  })

  it('in Russian: no Latin word of the interface - only data (red before: "Pay", "New line", "Edit trustline", "Close line"...)', async () => {
    setUiLocale('ru')
    await mountInspectors()
    expect(foreignWords('ru')).toEqual([])
  })

  it('in English: no Cyrillic word of the interface - only the names given as data', async () => {
    setUiLocale('en')
    await mountInspectors()
    expect(foreignWords('en')).toEqual([])
  })

  it('a switch re-renders the open inspectors without a remount: the same elements say the other language', async () => {
    setUiLocale('en')
    await mountInspectors()
    const pay = document.querySelector('[data-testid="node-card-send-payment"]')!
    const change = document.querySelector('[data-testid="edge-change-limit"]')!
    const before = [pay.textContent, change.textContent]
    setUiLocale('ru')
    await settle()
    expect(document.querySelector('[data-testid="node-card-send-payment"]'), 'the same element').toBe(pay)
    expect([pay.textContent, change.textContent]).not.toEqual(before)
    expect(foreignWords('ru')).toEqual([])
    setUiLocale('en')
    await settle()
    expect(foreignWords('en')).toEqual([])
  })

  it('the window shells of the inspectors are named in the language of the interface (both languages)', () => {
    const edge = { id: 1, type: 'edge-detail', data: { fromPid: 'alice', toPid: 'bob' } } as unknown as WindowInstance
    const card = { id: 2, type: 'node-card', data: { nodeId: 'alice' } } as unknown as WindowInstance
    const opts = { getNodeName: () => 'Алиса' }
    setUiLocale('ru')
    const ru = [resolveWindowSurfaceDescriptor(edge, opts).ariaLabel, resolveWindowSurfaceDescriptor(card, opts).ariaLabel].join(' ')
    expect((ru.match(/[A-Za-z_]+/g) ?? []).filter((w) => !RU_DATA_WORDS.has(w)), ru).toEqual([])
    setUiLocale('en')
    const en = [resolveWindowSurfaceDescriptor(edge, opts).ariaLabel, resolveWindowSurfaceDescriptor(card, opts).ariaLabel].join(' ')
    expect((en.match(/[Ѐ-ӿ]+/g) ?? []).filter((w) => !NAMES.has(w)), en).toEqual([])
  })
})

describe('046 wave 3 - one set of role words: the card says what the legend says', () => {
  const SCENE: GraphNode[] = [
    { id: 'd', name: 'Алиса', type: 'person', status: 'active', net_sign: -1, viz_shape_key: 'circle' },
    { id: 'c', name: 'Борис', type: 'person', status: 'active', net_sign: 1, viz_shape_key: 'circle' },
    { id: 'n', name: 'Вера', type: 'person', status: 'active', net_sign: 0, viz_shape_key: 'circle' },
    { id: 'u', name: 'Узел', type: 'person', status: 'active', net_sign: null, viz_shape_key: 'circle' },
    { id: 'f', name: 'Алиса', type: 'person', status: 'frozen', net_sign: 1, viz_shape_key: 'circle' },
    { id: 'h', name: 'Борис', type: 'hub', status: 'active', net_sign: 0, viz_shape_key: 'circle' },
  ] as GraphNode[]

  for (const locale of ['en', 'ru'] as const) {
    it(`${locale}: each word of the card's role is the legend's word for that entry (red before in en: "Even overall" / "even")`, async () => {
      setUiLocale(locale)
      const legend = mount(Legend, { nodes: SCENE, equivalent: 'UAH', palette: DATA_PALETTE_DEFAULT })
      await settle()
      const legendWord = (id: string) => legend.host.querySelector(`[data-legend-role="${id}"] .ds-legend__text`)!.textContent!.trim()
      for (const node of SCENE) {
        const card = mount(NodeCardOverlay, cardProps({ node, interactMode: false }))
        await settle()
        const el = card.host.querySelector('[data-testid="node-card-role"]')!
        const role = el.getAttribute('data-role')!
        // 049 S1 (decision 3): a node with no sign and no amount says the one message of an absent balance, not «unknown».
        // 049 S2: the role words are the state of the balance only; a freeze and a hub are said ONCE - by the meta.
        const expected = role === 'unknown' ? t('card.balanceUnavailable', {}, locale) : legendWord(role)
        expect(el.querySelector('.nco-role__words')!.textContent!.trim(), `${locale} ${node.id}`).toBe(expected)
        const meta = card.host.querySelector('[data-testid="node-card-meta"]')!.textContent!
        const row = card.host.querySelector('.ds-ov-node-card__facts')!.textContent!
        for (const [on, word] of [[node.status === 'frozen', legendWord('frozen')], [node.type === 'hub', legendWord('hub')]] as const) {
          if (!on) continue
          expect(meta, `${locale} ${node.id}: the meta says «${word}»`).toContain(word)
          expect(row.split(word).length - 1, `${locale} ${node.id}: «${word}» once in the row`).toBe(1)
        }
        card.app.unmount()
      }
    })
  }
})

describe('046 wave 3 - 048 item 13 (б): the line window explains the line in one sentence', () => {
  for (const locale of ['en', 'ru'] as const) {
    it(`${locale}: "A trusts B up to the limit - that is how much B may owe A", with the names of the line`, async () => {
      setUiLocale(locale)
      const { host } = mount(EdgeDetailPopup, popupProps())
      await settle()
      const el = host.querySelector('[data-testid="edge-trust-explanation"]')
      expect(el, 'the line is there').not.toBeNull()
      expect(el!.textContent!.trim()).toBe(t('edgeWindow.trustExplanation', { from: 'alice', to: 'bob' }))
      // The arrow of the window is creditor -> debtor: the creditor is named first, the debtor owes.
      expect(el!.textContent!.indexOf('alice')).toBeLessThan(el!.textContent!.indexOf('bob'))
    })
  }
  it('the sentence of the spec, word for word, in Russian', () => {
    expect(t('edgeWindow.trustExplanation', { from: 'A', to: 'B' }, 'ru')).toBe('A доверяет B до лимита — столько B может быть должен A.')
  })
})

type Actions = Parameters<typeof useInteractMode>[0]['actions']

function interactMode(over: Partial<Record<keyof Actions, unknown>> = {}) {
  const snapshot = ref<GraphSnapshot | null>({
    equivalent: 'UAH', generated_at: '2026-01-01T00:00:00Z',
    nodes: [
      { id: 'alice', name: 'Алиса', type: 'person', status: 'active' },
      { id: 'bob', name: 'Борис', type: 'person', status: 'active' },
      { id: 'vera', name: 'Вера', type: 'person', status: 'active' },
    ],
    links: [
      { source: 'bob', target: 'alice', used: '0.00', available: '10.00', trust_limit: '10.00', status: 'active' },
      { source: 'alice', target: 'vera', used: '0.00', available: '10.00', trust_limit: '10.00', status: 'active' },
    ],
  })
  const actions = {
    actionsDisabled: ref(false),
    sendPayment: vi.fn(async () => ({ ok: true, payment_id: 'p1', from_pid: 'alice', to_pid: 'bob', equivalent: 'UAH', amount: '1.00', status: 'COMMITTED', routes: [] })),
    createTrustline: vi.fn(async () => ({ ok: true, trustline_id: 't1', from_pid: 'bob', to_pid: 'vera', equivalent: 'UAH', limit: '10.00' })),
    updateTrustline: vi.fn(async () => ({ ok: true, trustline_id: 't2', old_limit: '10.00', new_limit: '20.00' })),
    closeTrustline: vi.fn(async () => ({ ok: true, trustline_id: 't2', status: 'closed' })),
    runClearing: vi.fn(async () => ({ ok: true, cleared_cycles: 1, total_cleared_amount: '5.00', cycles: [{ cleared_amount: '5.00', edges: [] }] })),
    fetchParticipants: vi.fn(async () => []),
    fetchTrustlines: vi.fn(async () => []),
    fetchPaymentTargets: vi.fn(async () => []),
    ...over,
  } as unknown as Actions
  return useInteractMode({ actions, runId: computed(() => 'run_test'), equivalent: computed(() => 'UAH'), snapshot })
}

describe('046 wave 3 - BACKLOG 046-3: what an action leaves behind follows the language of NOW', () => {
  it('the history re-renders on a switch (red before: it kept the language it was made in; 049 S2: no success notification any more)', async () => {
    setUiLocale('en')
    const im = interactMode()
    im.startPaymentFlow()
    im.selectNode('alice')
    im.selectNode('bob')
    await im.confirmPayment('1.00')
    im.dismissPaymentResult()
    im.cancel()
    im.startTrustlineFlow()
    im.selectNode('bob')
    im.selectNode('vera')
    await im.confirmTrustlineCreate('10.00')
    const history = mount(InteractHistoryLog, { entries: im.history, maxVisible: 8 })
    await settle()
    const items = () => [...history.host.querySelectorAll('.interact-history__text')].map((e) => e.textContent!.trim())
    expect(items(), 'counter-check: two lines, in English').toEqual([
      t('history.trustlineCreated', { from: 'Борис', to: 'Вера', limit: '10.00', unit: 'UAH' }, 'en'),
      t('history.payment', { amount: '1.00', unit: 'UAH', from: 'Алиса', to: 'Борис' }, 'en'),
    ])
    setUiLocale('ru')
    await settle()
    expect(items()).toEqual([
      t('history.trustlineCreated', { from: 'Борис', to: 'Вера', limit: '10.00', unit: 'UAH' }, 'ru'),
      t('history.payment', { amount: '1.00', unit: 'UAH', from: 'Алиса', to: 'Борис' }, 'ru'),
    ])
  })

  it('a plural history line (clearing) takes the form of the language of now', async () => {
    setUiLocale('ru')
    const im = interactMode()
    im.startClearingFlow()
    await im.confirmClearing()
    const history = mount(InteractHistoryLog, { entries: im.history, maxVisible: 8 })
    await settle()
    const text = () => history.host.querySelector('.interact-history__text')!.textContent!.trim()
    expect(text()).toBe('Клиринг: 1 круг, −5.00 UAH')
    setUiLocale('en')
    await settle()
    expect(text()).toBe('Clearing: 1 cycle, −5.00 UAH')
  })

  it('a window failure re-renders on a switch: the trust line window error and the clearing refusal', async () => {
    setUiLocale('en')
    const refuse = Object.assign(new Error('x'), { status: 409, code: 'TRUSTLINE_EXISTS', message: 'Active trustline already exists', details: null })
    const im = interactMode({
      createTrustline: vi.fn(async () => { throw refuse }),
      runClearing: vi.fn(async () => { throw Object.assign(new Error('y'), { status: 409, code: 'CONFLICT', message: 'busy', details: { reason: 'equivalent_inactive' } }) }),
    })
    im.startTrustlineFlow()
    im.selectNode('bob')
    im.selectNode('vera')
    await im.confirmTrustlineCreate('10.00')
    expect(im.state.error).toBe(t('actionError.TRUSTLINE_EXISTS', {}, 'en'))
    setUiLocale('ru')
    await settle()
    expect(im.state.error).toBe(t('actionError.TRUSTLINE_EXISTS', {}, 'ru'))

    setUiLocale('en')
    im.cancel()
    im.startClearingFlow()
    await im.confirmClearing()
    const panel = mount(ClearingPanel, { phase: im.phase.value, state: im.state, busy: false, equivalent: 'UAH', confirmClearing: vi.fn(), cancel: vi.fn() })
    await settle()
    const body = () => panel.host.textContent ?? ''
    expect(body()).toContain(t('clearing.equivalentInactive', {}, 'en'))
    setUiLocale('ru')
    await settle()
    expect(body()).toContain(t('clearing.equivalentInactive', {}, 'ru'))
  })

  it('a text the client composed (no run, no selection) follows the switch too - it is kept as its key, not as its text', async () => {
    setUiLocale('en')
    const im = interactMode()
    await im.confirmPayment('1.00') // no From / To: the client's own refusal
    expect(im.state.error).toBe(t('payment.noSelection', {}, 'en'))
    setUiLocale('ru')
    await settle()
    expect(im.state.error).toBe(t('payment.noSelection', {}, 'ru'))
  })
})

describe('046 wave 3 - BACKLOG 046-3: the failures the windows keep from the data loads follow the switch too', () => {
  const settleAll = async () => { for (let i = 0; i < 6; i += 1) { await Promise.resolve(); await nextTick() } }
  // As `useInteractActions` maps a rejected `fetch` (046 fix-delta F2): the code NETWORK.
  const network = () => Object.assign(new Error('Failed to fetch'), { status: 0, code: 'NETWORK', details: null })

  it('a trust lines load that failed: the notice of the line windows (live and a frozen copy) re-renders', async () => {
    setUiLocale('en')
    const im = interactMode({ fetchTrustlines: vi.fn(async () => { throw network() }) })
    await settleAll() // the cache asks for the lines of the run when it is made
    const live = im.trustlinesFetchState.value
    expect(live.kind, 'counter-check: the load failed').toBe('failed')
    expect(im.trustlinesLastError.value).toBe(t('actionError.NETWORK', {}, 'en'))
    // The figures of a pair the source did not answer for, as the root resolves them, frozen (a kept-alive line window).
    const frozen = freezeTrustlineFiguresSource(resolveTrustlineFiguresSource(live, false))
    setUiLocale('ru')
    await settleAll()
    expect(im.trustlinesLastError.value).toBe(t('actionError.NETWORK', {}, 'ru'))
    expect(trustlineFiguresNotice(resolveTrustlineFiguresSource(im.trustlinesFetchState.value, false))).toBe(t('trustlineSource.failed', { message: t('actionError.NETWORK', {}, 'ru') }, 'ru'))
    // The frozen copy was made in English; it is drawn in Russian now.
    expect(trustlineFiguresNotice(frozen)).toBe(t('trustlineSource.failed', { message: t('actionError.NETWORK', {}, 'ru') }, 'ru'))
  })

  it('a payment targets load that failed: its text re-renders', async () => {
    setUiLocale('en')
    const im = interactMode({ fetchPaymentTargets: vi.fn(async () => { throw network() }) })
    im.startPaymentFlow()
    im.selectNode('alice')
    await settleAll()
    expect(im.paymentTargetsLastError.value, 'counter-check: the load failed').toBe(t('actionError.NETWORK', {}, 'en'))
    setUiLocale('ru')
    await settleAll()
    expect(im.paymentTargetsLastError.value).toBe(t('actionError.NETWORK', {}, 'ru'))
  })

  it('the own failure of the client of the actions (no run) is kept as its key: the window re-renders it', async () => {
    setUiLocale('en')
    const snapshot = ref<GraphSnapshot | null>(null)
    const actions = useInteractActions({ httpConfig: ref({ apiBase: '/api/v1' }), runId: ref('') })
    const im = useInteractMode({ actions, runId: computed(() => ''), equivalent: computed(() => 'UAH'), snapshot })
    im.startTrustlineFlow()
    im.selectNode('alice')
    im.selectNode('bob')
    await im.confirmTrustlineCreate('10')
    expect(im.state.error, 'counter-check: the client refused (no run)').toBe(t('actionError.noRun', {}, 'en'))
    setUiLocale('ru')
    await settle()
    expect(im.state.error).toBe(t('actionError.noRun', {}, 'ru'))
  })
})

describe('046 wave 3 - BACKLOG 046-3: the warning the payment window keeps follows the switch (internal adversarial pass)', () => {
  it('"you cannot send a payment to yourself" re-renders on a switch', async () => {
    setUiLocale('en')
    const fsm = useInteractFSM({ snapshot: ref(null), findActiveTrustline: () => null })
    fsm.startPaymentFlowWithFrom('alice')
    const people = [{ pid: 'alice', name: 'Алиса' }, { pid: 'bob', name: 'Борис' }]
    const { host } = mount(ManualPaymentPanel, {
      phase: fsm.state.phase, state: fsm.state, unit: 'UAH', availableCapacity: '10.00', trustlinesLoading: false, paymentTargetsLoading: false,
      paymentTargetsLastError: null, paymentToTargetIds: new Set(['alice', 'bob']), trustlines: [], participants: people, busy: false,
      canSendPayment: true, confirmPayment: vi.fn(), cancel: vi.fn(), setFromPid: fsm.setPaymentFromPid, setToPid: fsm.setPaymentToPid,
    })
    await settle()
    fsm.setPaymentToPid('alice')
    await settle()
    const warn = () => host.querySelector('[data-testid="manual-payment-to-invalid-warn"]')?.textContent?.trim()
    expect(warn(), 'counter-check: the warning is shown').toBe(t('payment.selfPaymentReselect', {}, 'en'))
    setUiLocale('ru')
    await settle()
    expect(warn()).toBe(t('payment.selfPaymentReselect', {}, 'ru'))
  })
})
