/**
 * 046 wave 1 (T4602, narrowed by the owner's word 2026-10-18) - what the top bar gained, observed in the mounted bar:
 *
 *   1. the language switch sits in the service row right after the theme row (behind "Подробнее", not on the first
 *      view); choosing a language changes `<html lang>` and the texts of this wave without a remount, and is stored;
 *   2. BACKLOG 043-3: under a live run the scenario select is disabled and says the run has to be stopped first (its
 *      title and its accessible description); without a live run it is enabled and says nothing of the kind;
 *   3. a start refused by the global limit of runs shows "Try again" in the error line, which starts again (start-run);
 *      any other error line offers nothing;
 *   4. the stop reason of a stopped run is a text of the closed set in the language of the interface, an unknown one
 *      the general text with the value; the raw `source: reason` is the line's title.
 *
 * LIMITS: the other texts of the bar are English literals on purpose (they are under the owner's review; spec 046,
 * Changelog 2026-10-18); what the app does on start-run is `useSimulatorApp.autostart.p043.test.ts`; the layout of the
 * row with the switch is the stand (`top-bar-long.qa.ts`).
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref } from 'vue'

import type { RunStatus } from '../api/simulatorTypes'
import { provideTopBarContext, type TopBarContext } from '../composables/useTopBarContext'
import { setUiLocale, UI_LOCALE_STORAGE_KEY } from '../i18n/locale'
import TopBar from './TopBar.vue'

afterEach(() => {
  setUiLocale('en')
  window.localStorage.clear()
})

function context(over: Partial<TopBarContext> = {}): TopBarContext {
  return {
    apiMode: ref('real'), isTestMode: ref(false), uiTheme: ref('saas'),
    loadingScenarios: ref(false), scenarios: ref([{ scenario_id: 'sc-1', label: 'Riverside town' }, { scenario_id: 'sc-2', label: 'Two' }]),
    selectedScenarioId: ref('sc-1'), intensityPercent: ref(30), runId: ref('run-1'), runStatus: ref(null), sseState: ref('open'),
    lastError: ref(null),
    runStats: ref({ startedAtMs: 0, attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} }),
    accessToken: ref(''),
    ...over,
  } as TopBarContext
}

const status = (state: string, extra: Partial<RunStatus> = {}) => ({ run_id: 'run-1', scenario_id: 'sc-1', state, ...extra }) as RunStatus

async function mount(ctx: TopBarContext, detailsOpen = true) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const startRun = vi.fn()
  const app = createApp({ setup() { provideTopBarContext(ctx); return () => h(TopBar, { detailsOpen, onStartRun: startRun }) } })
  app.mount(host)
  await nextTick()
  return { host, startRun, done: () => { app.unmount(); host.remove() } }
}

const text = (el: Element | null | undefined) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()

describe('046 wave 1 (1) - the language switch in the service row', () => {
  it('is the next control after the theme row, inside the mode row; absent from the first view', async () => {
    const off = await mount(context(), false)
    expect(off.host.querySelector('[data-testid="language-switch"]'), 'not on the first view (043: at most 7 stops)').toBeNull()
    off.done()

    const { host, done } = await mount(context())
    const sw = host.querySelector('[data-testid="language-switch"]')
    expect(sw, 'rendered behind "Подробнее"').not.toBeNull()
    expect(sw!.parentElement!.classList.contains('tb-mode-row')).toBe(true)
    expect(sw!.previousElementSibling?.classList.contains('tb-theme-row'), 'right after the theme row closes').toBe(true)
    done()
  })

  it('choosing ru changes <html lang> and the texts of this wave in place, and stores the choice; en back', async () => {
    setUiLocale('en')
    const ctx = context({ runStatus: ref(status('stopped', { stop_source: 'ui', stop_reason: 'user_stop' })), lastError: ref('Boom'), runId: ref(null) })
    const { host, done } = await mount(ctx)
    const reason = () => [...(host.querySelector('[data-testid="stop-reason"]')?.children ?? [])].map(text).join(' | ')
    expect(reason()).toBe('Stop | stopped with the Stop button')
    // 048 S3: the visible «Error» label before the sentence is gone (decision 3); the line's accessible name still follows
    // the language - the witness of the switch here.
    const errLabel = () => host.querySelector('[data-testid="top-error"]')?.getAttribute('aria-label') ?? ''
    expect(errLabel()).toBe('Error')

    const switchBefore = sw0(host)
    const select = host.querySelector<HTMLSelectElement>('[data-testid="language-select"]')!
    select.value = 'ru'
    select.dispatchEvent(new Event('change'))
    await nextTick()
    expect(document.documentElement.lang).toBe('ru')
    expect(window.localStorage.getItem(UI_LOCALE_STORAGE_KEY), 'stored: the next load follows it').toBe('ru')
    expect(reason()).toBe('Остановка | остановлен кнопкой «Стоп»')
    expect(errLabel()).toBe('Ошибка')
    expect(sw0(host), 'the same element: the bar was not mounted again').toBe(switchBefore)

    select.value = 'en'
    select.dispatchEvent(new Event('change'))
    await nextTick()
    expect([document.documentElement.lang, reason()]).toEqual(['en', 'Stop | stopped with the Stop button'])
    done()
  })
})

function sw0(host: HTMLElement) {
  return host.querySelector('[data-testid="language-switch"]')
}

describe('046 wave 1 (2) - BACKLOG 043-3: the scenario cannot be changed under a live run', () => {
  // 048 S2 (amendment 8): the scenario select is in the row of the own run, the administrator's - an admin token here.
  const admin = { accessToken: ref('admin-token') } as Partial<TopBarContext>
  for (const state of ['running', 'paused', 'created', 'stopping']) {
    it(`a ${state} run: the select is disabled and the line says why (en and ru)`, async () => {
      const { host, done } = await mount(context({ ...admin, runStatus: ref(status(state)) }))
      const select = host.querySelector<HTMLSelectElement>('[data-testid="scenario-select"]')!
      expect(select.disabled).toBe(true)
      const line = host.querySelector('[data-testid="scenario-locked"]')
      expect(text(line)).toBe('Stop the run to choose another scenario.')
      expect(select.getAttribute('aria-describedby')).toBe(line!.id)
      // Said to a pointer too: the select's title is its full option text and the reason.
      expect(select.getAttribute('title')).toBe('Riverside town (sc-1) — Stop the run to choose another scenario.')
      expect(line!.classList.contains('tb-visually-hidden'), 'not a visible line: the row does not grow under a live run').toBe(true)
      setUiLocale('ru')
      await nextTick()
      expect(text(host.querySelector('[data-testid="scenario-locked"]'))).toBe('Остановите прогон, чтобы выбрать другой сценарий.')
      done()
    })
  }

  it('a run whose status is not read yet counts as live (the select is locked, as Start is hidden)', async () => {
    const { host, done } = await mount(context({ ...admin, runStatus: ref(null) }))
    expect(host.querySelector<HTMLSelectElement>('[data-testid="scenario-select"]')!.disabled).toBe(true)
    done()
  })

  it('counter-check: no run, a stopped and an errored run - the select is enabled, no line', async () => {
    for (const over of [{ runId: ref(null) }, { runStatus: ref(status('stopped')) }, { runStatus: ref(status('error')) }] as Partial<TopBarContext>[]) {
      const { host, done } = await mount(context({ ...admin, ...over }))
      const select = host.querySelector<HTMLSelectElement>('[data-testid="scenario-select"]')!
      expect(select.disabled).toBe(false)
      expect(select.hasAttribute('aria-describedby')).toBe(false)
      expect(host.querySelector('[data-testid="scenario-locked"]')).toBeNull()
      expect(select.getAttribute('title'), 'the full option text only').toBe('Riverside town (sc-1)')
      done()
    }
  })
})

describe('046 wave 1 (3) - "Try again" on a start the limit of runs refused', () => {
  it('shown in the error line (first view too) and starts again', async () => {
    const { host, startRun, done } = await mount(context({ runId: ref(null), lastError: ref('All 10 places…'), startRetryable: ref(true) }), false)
    const retry = host.querySelector<HTMLButtonElement>('[data-testid="top-error"] [data-testid="start-retry"]')
    expect(text(retry)).toBe('Try again')
    retry!.click()
    expect(startRun).toHaveBeenCalledTimes(1)
    setUiLocale('ru')
    await nextTick()
    expect(text(host.querySelector('[data-testid="start-retry"]'))).toBe('Повторить')
    done()
  })

  it('counter-check: an error line that is not that refusal offers nothing', async () => {
    for (const over of [{ startRetryable: ref(false) }, {}] as Partial<TopBarContext>[]) {
      const { host, done } = await mount(context({ runId: ref(null), lastError: ref('HTTP 503 for /simulator/runs'), ...over }), false)
      expect(host.querySelector('[data-testid="top-error"]'), 'the error line is there').not.toBeNull()
      expect(host.querySelector('[data-testid="start-retry"]')).toBeNull()
      done()
    }
  })
})

describe('046 wave 1 (4) - the stop reason in words', () => {
  const reasonOf = async (stop_reason: string | null, stop_source: string | null = 'ui') => {
    const { host, done } = await mount(context({ runStatus: ref(status('stopped', { stop_reason, stop_source })) }))
    const el = host.querySelector('[data-testid="stop-reason"]')
    const out = { value: text(el?.querySelector('.ds-value')), title: el?.getAttribute('title') ?? null, present: !!el }
    done()
    return out
  }

  it('the closed set, en and ru', async () => {
    const cases: Array<[string, string, string]> = [
      ['user_stop', 'stopped with the Stop button', 'остановлен кнопкой «Стоп»'],
      ['admin_stop', 'stopped by the operator', 'остановлен оператором'],
      ['admin_stop_all', 'stopped by the operator (all runs)', 'остановлен оператором (все прогоны)'],
      ['idle_no_subscribers', 'stopped by the server: nobody watched it', 'остановлен сервером: его никто не смотрел'],
    ]
    for (const [reason, en, ru] of cases) {
      setUiLocale('en')
      expect((await reasonOf(reason)).value, reason).toBe(en)
      setUiLocale('ru')
      expect((await reasonOf(reason)).value, reason).toBe(ru)
    }
  })

  // 049 S2 (decision 7): a code is not drawn in brackets - the raw source and reason stay the title (red before:
  // «stopped (maintenance_window)»).
  it('an unknown reason: the general text; the raw source and reason are the title, not drawn', async () => {
    setUiLocale('en')
    expect(await reasonOf('maintenance_window', 'cli')).toEqual({ value: 'stopped', title: 'cli: maintenance_window', present: true })
    setUiLocale('ru')
    expect((await reasonOf('maintenance_window', 'cli')).value).toBe('остановлен')
    // The key of the general text is not a reason: a server that sends the word "unknown" gets the general text, and the title.
    expect(await reasonOf('unknown')).toMatchObject({ value: 'остановлен', title: 'ui: unknown' })
  })

  it('no reason (a source alone, or nothing): no line - there is nothing readable to say', async () => {
    expect((await reasonOf(null, 'ui')).present).toBe(false)
    expect((await reasonOf('  ', null)).present).toBe(false)
  })
})
