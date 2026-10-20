/**
 * 049 S2 - the two controls the slice adds to «Подробнее», mounted as the root mounts the top bar:
 *  - decision 10: the visitor's tempo, three steps by WORDS only («медленно / обычно / быстро» = 2 / 5 / 15 %). The step
 *    shown is the RUN's tempo (`intensity_percent` of its status); a tempo that is none of the three shows no step (no
 *    fourth); the administrator keeps the percent field with Apply and gets no steps; an ended run gets none;
 *  - decision 5: «Показывать события» - a checkbox whose state is the root's preference (`eventsFeedOn`), off by default.
 * What this does NOT see: that a step reaches the run (the root's `onSetTempo` -> `applyIntensity`:
 * `SimulatorAppRoot.interact.test.ts`; the run's rate: the live test) and what the switch does to the history
 * (`eventFeed.p049s2.test.ts`).
 */
import { createApp, h, nextTick, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { RunStatus } from '../api/simulatorTypes'
import { provideTopBarContext, type TopBarContext } from '../composables/useTopBarContext'
import { setUiLocale } from '../i18n/locale'
import TopBar from './TopBar.vue'

const status = (state: string, intensity_percent?: number) => ({ run_id: 'run-1', scenario_id: 'sc-1', state, intensity_percent }) as RunStatus

function context(over: Partial<TopBarContext> = {}): TopBarContext {
  return {
    apiMode: ref('real'), isTestMode: ref(false), uiTheme: ref('saas'),
    loadingScenarios: ref(false), scenarios: ref([{ scenario_id: 'sc-1', label: 'One' }]),
    selectedScenarioId: ref('sc-1'), intensityPercent: ref(5), runId: ref('run-1'), runStatus: ref(status('running', 5)), sseState: ref('open'),
    lastError: ref(null),
    runStats: ref({ startedAtMs: 0, attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} }),
    accessToken: ref(null),
    ...over,
  } as TopBarContext
}

function mountTopBar(ctx: TopBarContext, props: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const onSetTempo = vi.fn()
  const onToggleEventsFeed = vi.fn()
  const app = createApp({ setup() { provideTopBarContext(ctx); return () => h(TopBar, { detailsOpen: true, onSetTempo, onToggleEventsFeed, ...props }) } })
  app.mount(host)
  return { host, onSetTempo, onToggleEventsFeed, done: () => { app.unmount(); host.remove() } }
}

const steps = (host: HTMLElement) => [...host.querySelectorAll<HTMLButtonElement>('[data-testid="tempo-steps"] button')]
const pressed = (host: HTMLElement) => steps(host).filter((b) => b.getAttribute('aria-pressed') === 'true').map((b) => b.dataset.testid)

afterEach(() => setUiLocale('en'))

describe('049 S2 (decision 10): the visitor\'s tempo by words', () => {
  it('three steps, the run\'s tempo pressed; a tempo none of the three presses nothing (no fourth step)', async () => {
    for (const [tempo, expected] of [[2, ['tempo-step-slow']], [5, ['tempo-step-normal']], [15, ['tempo-step-fast']], [30, []], [undefined, []]] as const) {
      const { host, done } = mountTopBar(context({ runStatus: ref(status('running', tempo)) }))
      await nextTick()
      expect(steps(host).map((b) => b.dataset.testid), `tempo ${tempo}`).toEqual(['tempo-step-slow', 'tempo-step-normal', 'tempo-step-fast'])
      expect(pressed(host), `tempo ${tempo}`).toEqual(expected)
      done()
    }
  })

  it('words only, in the language of the interface: no figure, no percent, no promise per second', async () => {
    for (const [locale, words] of [['ru', ['медленно', 'обычно', 'быстро']], ['en', ['slow', 'normal', 'fast']]] as const) {
      setUiLocale(locale)
      const { host, done } = mountTopBar(context())
      await nextTick()
      expect(steps(host).map((b) => b.textContent!.trim()), locale).toEqual(words)
      const group = host.querySelector('[data-testid="tempo-steps"]')!
      expect(group.textContent, `${locale}: no figure in the control`).not.toMatch(/\d|%/)
      done()
    }
  })

  it('a step asks for its percent (2 / 5 / 15) - the root applies it to the run', async () => {
    const { host, onSetTempo, done } = mountTopBar(context())
    await nextTick()
    for (const b of steps(host)) b.click()
    expect(onSetTempo.mock.calls.map((c) => c[0])).toEqual([2, 5, 15])
    done()
  })

  it('a paused run has the steps; an ended run and the administrator have none (the administrator keeps the percent field)', async () => {
    const paused = mountTopBar(context({ runStatus: ref(status('paused', 2)) }))
    await nextTick()
    expect(pressed(paused.host)).toEqual(['tempo-step-slow'])
    paused.done()
    const ended = mountTopBar(context({ runStatus: ref(status('stopped', 5)) }))
    await nextTick()
    expect(steps(ended.host)).toHaveLength(0)
    ended.done()
    const admin = mountTopBar(context({ accessToken: ref('admin-token') }))
    await nextTick()
    expect(steps(admin.host)).toHaveLength(0)
    expect(admin.host.querySelector('[data-testid="intensity-input"]'), 'counter-check: the administrator\'s field').not.toBeNull()
    admin.done()
  })
})

describe('049 S2 (decision 5): «Показывать события»', () => {
  it('off unless the root says on; a change asks the root to toggle', async () => {
    const off = mountTopBar(context())
    await nextTick()
    const box = off.host.querySelector<HTMLInputElement>('[data-testid="events-feed-toggle"]')!
    expect(box.type).toBe('checkbox')
    expect(box.checked, 'off by default').toBe(false)
    expect(box.closest('label')!.textContent!.trim()).toBe('Show events')
    box.click()
    expect(off.onToggleEventsFeed).toHaveBeenCalledTimes(1)
    off.done()
    const on = mountTopBar(context(), { eventsFeedOn: true })
    await nextTick()
    expect(on.host.querySelector<HTMLInputElement>('[data-testid="events-feed-toggle"]')!.checked).toBe(true)
    on.done()
  })

  it('behind «Подробнее» only: the first view has no switch and no steps', async () => {
    const { host, done } = mountTopBar(context(), { detailsOpen: false })
    await nextTick()
    expect(host.querySelector('[data-testid="events-feed-toggle"]')).toBeNull()
    expect(host.querySelector('[data-testid="tempo-steps"]')).toBeNull()
    done()
  })
})
