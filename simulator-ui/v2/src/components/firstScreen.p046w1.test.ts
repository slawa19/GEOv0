/**
 * 046 wave 1, full (T4602, after 048 S2): the first screen speaks ONE language. Mounted as the root mounts them: the top
 * bar (first row, "Подробнее" for a visitor and for the administrator, the error line with "Try again", the stop reason,
 * the stall alert, "No scenarios…"), the action bar in each of its states, the bottom bar with its "Подробнее" row, the
 * network figures, the legend, "Find on graph", the recent actions, the line tooltip and the two notifications.
 *
 * The rule is structural (AGENTS.md §9: no list of words to look for): everything the screen SAYS - visible text and the
 * `aria-label` / `title` / `placeholder` a keyboard or screen-reader user meets - is read together; under `ru` it holds
 * no Latin word except data, under `en` no Cyrillic at all. Data is made Cyrillic-free where it could hide a leftover:
 * participant and scenario names here are Cyrillic for `ru` checks and Latin for `en` checks would hide nothing, so the
 * names are Cyrillic in both runs and the `en` check excludes exactly the names it was given (by value, not by pattern).
 * Data words allowed under `ru`: the equivalent code, the protocol's name `GEO` (the parity guard's `RU_LATIN_ALLOWED`),
 * the participant ids an unnamed node prints, and the run's server code in the error line.
 *
 * Red before the wave on `main` `efc12f62`: the first screen mixed both languages ("Подробнее", "живёт", "Поток" beside
 * "Send Payment", "Reset view", "Inspect graph", "Overall balance", "Total debt" in Russian...).
 * What this does NOT see: the developer's controls of `?debug=1` (not the visitor's screen: "Источник", Quality, SSE,
 * the playlist and the analytics dock stay as they are - spec 046 changelog), the inspector windows (wave 3), the width
 * of the Russian texts (the browser test `e2e/p046-w1-full.spec.ts` and the stand's `top-bar-long.qa.ts` measure it).
 */
import { computed, createApp, h, nextTick, ref, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { RunStatus } from '../api/simulatorTypes'
import { provideTopBarContext, type TopBarContext } from '../composables/useTopBarContext'
import { setUiLocale, UI_LOCALE_ENDONYMS, type UiLocale } from '../i18n/locale'
import { t } from '../i18n/t'
import ActionBar from './ActionBar.vue'
import BottomBar from './BottomBar.vue'
import EdgeTooltip from './EdgeTooltip.vue'
import GraphNavigator from './GraphNavigator.vue'
import InteractHistoryLog from './InteractHistoryLog.vue'
import Legend from './Legend.vue'
import SuccessToast from './SuccessToast.vue'
import SystemBalanceBar from './SystemBalanceBar.vue'
import TopBar from './TopBar.vue'

const ADMIN = 'admin-token'
const SCENARIO = { scenario_id: 'sc-1', name: 'Город у реки' }
const NODES = [
  { id: 'alice', name: 'Алиса', type: 'person', status: 'active', net_sign: -1, viz_shape_key: 'circle' },
  { id: 'bob', name: 'Борис', type: 'business', status: 'frozen', net_sign: 1, viz_shape_key: 'rounded-rect' },
  { id: 'hub', name: 'Узел', type: 'hub', status: 'active', net_sign: 0, viz_shape_key: 'circle' },
  { id: 'dan', name: 'Данил', type: 'person', status: 'active', net_sign: null, viz_shape_key: 'circle' },
]
const LINKS = [{ source: 'alice', target: 'bob', trust_limit: '100' }]
/** Each language is named in itself in the language switch (names, not dictionary text: `UI_LOCALE_ENDONYMS`). */
const ENDONYMS = Object.values(UI_LOCALE_ENDONYMS)
const NAMES = new Set(['Город', 'у', 'реки', 'Алиса', 'Борис', 'Узел', 'Данил', ...ENDONYMS])
/**
 * Data under `ru`: the codes of the equivalents, the protocol's name and the key `Esc` (the parity guard's allow-list),
 * the scenario's id the select prints beside its name (`sc-1`), the raw stop reason in the stop line's `title` (kept for
 * correlation, AGENTS.md section 12) - and the language names.
 */
const RU_DATA_WORDS = new Set(['UAH', 'EUR', 'GEO', 'Esc', 'sc', 'admin_stop', ...ENDONYMS])

afterEach(() => {
  setUiLocale('en')
  window.localStorage.clear()
  document.body.innerHTML = ''
})

/** Everything the mounted screen says: visible text and the accessible names, titles and placeholders. */
function said(): string {
  // Text nodes one by one: `textContent` glues the options of a select into one word ("UAHEUR").
  const parts: string[] = []
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
  for (let n = walker.nextNode(); n; n = walker.nextNode()) parts.push(n.nodeValue ?? '')
  for (const el of Array.from(document.body.querySelectorAll('*'))) {
    for (const a of ['aria-label', 'title', 'placeholder']) {
      const v = el.getAttribute(a)
      if (v) parts.push(v)
    }
  }
  return parts.join(' \n ').replace(/\s+/g, ' ')
}

/** What the screen says in the OTHER language than `locale`: Latin words under `ru`, Cyrillic words under `en`. */
function foreignWords(locale: UiLocale): string[] {
  const text = said()
  if (locale === 'ru') return (text.match(/[A-Za-z_]+/g) ?? []).filter((w) => !RU_DATA_WORDS.has(w))
  return (text.match(/[Ѐ-ӿ]+/g) ?? []).filter((w) => !NAMES.has(w))
}

function context(over: Partial<TopBarContext> = {}): TopBarContext {
  return {
    apiMode: ref('real'), isTestMode: ref(false), uiTheme: ref('saas'),
    loadingScenarios: ref(false), scenarios: ref([SCENARIO]),
    selectedScenarioId: ref('sc-1'), intensityPercent: ref(30), runId: ref('run-1'),
    runStatus: ref({ run_id: 'run-1', scenario_id: 'sc-1', state: 'running', consec_all_rejected_ticks: 5 } as RunStatus),
    sseState: ref('open'), lastError: ref(null), startRetryable: ref(false),
    runStats: ref({ startedAtMs: 0, attempts: 4, committed: 3, rejected: 1, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} }),
    accessToken: ref(null),
    ...over,
  } as TopBarContext
}

function mount(component: Component, props: Record<string, unknown>, ctx?: TopBarContext) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ setup() { if (ctx) provideTopBarContext(ctx); return () => h(component, props) } })
  app.mount(host)
  return host
}

async function settle() {
  for (let i = 0; i < 4; i += 1) await nextTick()
}

const actionBarProps = (over: Record<string, unknown> = {}) => ({
  phase: 'idle', busy: false, actionsDisabled: false, runTerminal: false,
  startPaymentFlow: vi.fn(), startTrustlineFlow: vi.fn(), startClearingFlow: vi.fn(), ...over,
})

const bottomBarProps = () => ({
  apiMode: 'real', equivalentOptions: ['UAH', 'EUR'], eq: 'UAH', layoutMode: 'admin-force', quality: 'med',
  showResetView: true, resetView: vi.fn(), detailsOpen: true, debug: false,
})

/**
 * Every state of the first screen this file mounts at once, in one language: the screen a visitor and an administrator
 * meet, with the stall, an error line offering "Try again", a stop reason, an empty catalogue, the action bar in each
 * of its states and the line tooltip.
 */
async function mountFirstScreen(): Promise<void> {
  // The top bar: the administrator's "Подробнее" (it holds the visitor's and more), a stall and an error with "Try again".
  // The error line's holder renders its text in the current language (`useSimulatorRealMode.ts`); here a computed does.
  // 048 S3: with its request id - the id's line is the interface's words too («код запроса» / «request id»; the id is data).
  mount(TopBar, { detailsOpen: true }, context({ accessToken: ref(ADMIN), lastError: computed(() => t('runStart.limitBusy')), lastErrorRef: ref('1234-5678'), startRetryable: ref(true) } as Partial<TopBarContext>))
  // A visitor's first row only, on a paused run (the other words of the flow button and of the state).
  mount(TopBar, { detailsOpen: false }, context({ runStatus: ref({ run_id: 'run-1', scenario_id: 'sc-1', state: 'paused' } as RunStatus) }))
  // A stopped run with a reason, and the administrator's empty catalogue ("No scenarios…").
  mount(TopBar, { detailsOpen: true }, context({
    accessToken: ref(ADMIN), scenarios: ref([]), runId: ref(null),
    runStatus: ref({ run_id: 'run-1', scenario_id: 'sc-1', state: 'stopped', stop_reason: 'admin_stop' } as RunStatus),
    runStats: ref({ startedAtMs: 0, attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} }),
  }))
  // The offline snapshot (the developer's source, but its name and state are on the first row).
  mount(TopBar, { detailsOpen: false }, context({ apiMode: ref('fixtures') }))
  for (const over of [{}, { busy: true }, { busy: true, cancelling: true }, { phase: 'picking-payment-from' }, { actionsDisabled: true }, { runTerminal: true }]) {
    mount(ActionBar, actionBarProps(over))
  }
  mount(BottomBar, bottomBarProps())
  const balance = { totalUsed: '12.00', totalAvailable: '88.00', activeTrustlines: 3, activeParticipants: 4, utilization: 0.12, equivalent: 'UAH' }
  mount(SystemBalanceBar, { balance, variant: 'full' })
  mount(Legend, { nodes: NODES, equivalent: 'UAH', palette: { debtor: '#a00', creditor: '#00a', neutral: '#ccc', frozen: '#444', hub: '#0e0' } })
  const nav = mount(GraphNavigator, { nodes: NODES, links: LINKS, edgeInspectDisabled: true })
  nav.querySelector('details')!.open = true
  // 046 wave 3: a history line is kept as its key and data (BACKLOG 046-3) - so it is the interface's words too.
  mount(InteractHistoryLog, { entries: [{ id: 'h1', icon: '💸', line: { key: 'history.payment', vars: { amount: '1.00', unit: 'UAH', from: 'Алиса', to: 'Борис' } }, timeText: '12:00' }] })
  mount(EdgeTooltip, {
    style: {}, getNodeName: (id: string) => NODES.find((n) => n.id === id)?.name ?? null,
    edge: { key: 'alice->bob', fromId: 'alice', toId: 'bob', amountText: '', trustLimit: '100.00', used: '12.00', available: '-3.00', equivalent: 'UAH' },
  })
  mount(SuccessToast, { message: 'Алиса', onDismiss: vi.fn() })
  await settle()
}

describe('046 wave 1, full - the first screen in one language', () => {
  it('counter-check: the screen is mounted - every surface above is in the DOM (a check over an empty page would pass)', async () => {
    await mountFirstScreen()
    for (const id of [
      'network-state', 'flow-toggle', 'details-toggle', 'top-details-row', 'own-run-row', 'run-payments', 'stop-reason', 'top-error',
      'start-retry', 'capacity-stall', 'no-scenarios', 'action-bar', 'actionbar-busy-hint', 'actionbar-locked-hint', 'bottom-details-row',
      'layout-select', 'reset-view', 'system-balance', 'legend', 'graph-navigator', 'recent-actions', 'edge-tooltip', 'success-toast',
      // 048 S3: the error toast is gone (a window's failure is its block); the error line's request id is its own line.
      'error-request-id',
    ]) expect(document.querySelector(`[data-testid="${id}"]`), id).not.toBeNull()
  })

  it('in Russian: no Latin word of the interface - only data (red before: "Send Payment", "Reset view", "Overall balance"...)', async () => {
    setUiLocale('ru')
    await mountFirstScreen()
    expect(foreignWords('ru')).toEqual([])
  })

  it('in English: no Cyrillic word of the interface - only the names given as data (red before: "Подробнее", "живёт", "Поток"...)', async () => {
    setUiLocale('en')
    await mountFirstScreen()
    expect(foreignWords('en')).toEqual([])
  })

  it('a switch re-renders the mounted screen without a remount: the same DOM says the other language', async () => {
    setUiLocale('en')
    await mountFirstScreen()
    const toggle = document.querySelector('[data-testid="details-toggle"]')!
    expect(toggle.textContent?.trim()).toMatch(/^Details/)
    setUiLocale('ru')
    await settle()
    expect(document.querySelector('[data-testid="details-toggle"]'), 'the same element').toBe(toggle)
    expect(toggle.textContent?.trim()).toMatch(/^Подробнее/)
    expect(foreignWords('ru')).toEqual([])
  })
})

describe('046 wave 1, full - the words of 048 §G and item 13 (a)', () => {
  it('the three action hints say what the action results in, not the button\'s name again (both languages)', async () => {
    for (const locale of ['en', 'ru'] as const) {
      setUiLocale(locale)
      const host = mount(ActionBar, actionBarProps())
      await settle()
      for (const [id, name, hint] of [
        ['actionbar-payment', 'actions.payment', 'actions.paymentHint'],
        ['actionbar-trustline', 'actions.trustline', 'actions.trustlineHint'],
        ['actionbar-clearing', 'actions.clearing', 'actions.clearingHint'],
      ] as const) {
        const b = host.querySelector<HTMLButtonElement>(`[data-testid="${id}"]`)!
        expect(b.textContent?.trim(), `${locale} ${id}: the name`).toBe(t(name))
        expect(b.title, `${locale} ${id}: the result`).toBe(t(hint))
        // Not the name again: the hint does not contain the button's own words (was "Send a manual payment", "Run clearing").
        expect(b.title.toLowerCase(), `${locale} ${id}: not the name`).not.toContain(t(name).toLowerCase())
      }
      document.body.innerHTML = ''
    }
  })

  it('048 S2 hand-over P2-2: without a run the reason names the reload, never a Start the visitor has not got', async () => {
    for (const locale of ['en', 'ru'] as const) {
      setUiLocale(locale)
      const host = mount(ActionBar, actionBarProps({ runTerminal: true }))
      await settle()
      const titles = [...host.querySelectorAll<HTMLButtonElement>('button')].map((b) => b.title)
      expect(titles, locale).toEqual([t('actions.noRun'), t('actions.noRun'), t('actions.noRun')])
      expect(titles[0], `${locale}: no Start / Старт`).not.toMatch(/Start|Старт/)
      document.body.innerHTML = ''
    }
  })

  it('the payments of the run are a sentence (§G): "a of b went through", and "No payments yet" at zero attempts', async () => {
    setUiLocale('ru')
    const host = mount(TopBar, { detailsOpen: true }, context({ accessToken: ref(ADMIN) }))
    await settle()
    expect(host.querySelector('[data-testid="run-payments"]')!.textContent?.trim()).toBe('Платежи: 3 из 4 прошли')
    document.body.innerHTML = ''
    const zero = mount(TopBar, { detailsOpen: true }, context({
      accessToken: ref(ADMIN),
      runStats: ref({ startedAtMs: 0, attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} }),
    }))
    await settle()
    expect(zero.querySelector('[data-testid="run-payments"]')!.textContent?.trim()).toBe('Платежей ещё не было')
    expect(zero.querySelector('[data-testid="run-payments"]')!.textContent).not.toContain('%')
  })

  it('the stall alert is the one sentence of §G, with its action', async () => {
    for (const locale of ['en', 'ru'] as const) {
      setUiLocale(locale)
      const host = mount(TopBar, { detailsOpen: false }, context())
      await settle()
      const stall = host.querySelector('[data-testid="capacity-stall"]')!
      expect(stall.querySelector('.ds-value')!.textContent?.trim(), locale).toBe(t('topBar.stall'))
      expect(stall.getAttribute('aria-label'), locale).toBe(t('topBar.stallAria'))
      document.body.innerHTML = ''
    }
    expect(t('topBar.stall', undefined, 'ru')).toBe('Платежи не проходят: лимиты исчерпаны — проведите клиринг или увеличьте лимит')
    expect(t('topBar.stall', undefined, 'en')).toBe('Payments are not going through: limits are exhausted — run clearing or raise a limit')
  })
})
