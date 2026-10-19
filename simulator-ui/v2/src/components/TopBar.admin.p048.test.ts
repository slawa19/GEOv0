/**
 * 048 S1 (decisions 2, 4 and 5) - the top bar for its addressees, mounted and used as a person uses it.
 *
 *  - the visitor: the capacity-stall alert is on the first view, only while payments stall; a lost stream is said in
 *    the state of the network;
 *  - the developer (`debug`): the SSE badge.
 *
 * 048 S2 (amendments 8-12): "Admin ▾" is gone, and with it the tests of this file that used it - the administrator's
 * tools inside the menu, the catalogue refresh's absence from its tools row, the analytics toggle in it and the five
 * tests of the run files (a removed contract: the run files are the API's). What the top bar shows each addressee now,
 * and the analytics toggle under `debug`, is `TopBar.p048s2.test.ts`.
 * What this does NOT see: the browser (`e2e/p048-s1-addressees.spec.ts`, `e2e/p048-s2-edge-cases-out.spec.ts`).
 */
import { createApp, h, nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import type { RunStatus } from '../api/simulatorTypes'
import { provideTopBarContext, type TopBarContext } from '../composables/useTopBarContext'
import { t } from '../i18n/t'
import TopBar from './TopBar.vue'

function context(over: Partial<TopBarContext> = {}): TopBarContext {
  return {
    apiMode: ref('real'), isTestMode: ref(false), uiTheme: ref('saas'),
    loadingScenarios: ref(false), scenarios: ref([{ scenario_id: 'sc-1', label: 'One' }]),
    selectedScenarioId: ref('sc-1'), intensityPercent: ref(30), runId: ref('run-1'),
    runStatus: ref({ run_id: 'run-1', scenario_id: 'sc-1', state: 'running' } as RunStatus), sseState: ref('open'),
    lastError: ref(null),
    runStats: ref({ startedAtMs: 0, attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} }),
    accessToken: ref('admin-token'),
    ...over,
  } as TopBarContext
}

function mountTopBar(ctx: TopBarContext, props: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const onToggleAnalytics = vi.fn()
  const app = createApp({
    setup() {
      provideTopBarContext(ctx)
      return () => h(TopBar, { detailsOpen: true, onToggleAnalytics, ...props })
    },
  })
  app.mount(host)
  return { host, onToggleAnalytics, done: () => { app.unmount(); host.remove() } }
}

describe('048 S1 - the capacity stall is on the first view (decision 2)', () => {
  const stalled = (ticks: number) => ref({ run_id: 'run-1', scenario_id: 'sc-1', state: 'running', consec_all_rejected_ticks: ticks } as RunStatus)

  it('reproducer (г), unit half: shown with "Подробнее" OFF once the run has stalled for the threshold; absent before', async () => {
    // Anti-vacuum first: without a stall (and below the threshold) there is no alert, with Details off and on.
    for (const ticks of [0, 2]) {
      for (const detailsOpen of [false, true]) {
        const { host, done } = mountTopBar(context({ runStatus: stalled(ticks) }), { detailsOpen })
        await nextTick()
        expect(host.querySelector('[data-testid="capacity-stall"]'), `${ticks} ticks, details ${detailsOpen}: no alert`).toBeNull()
        done()
      }
    }
    for (const detailsOpen of [false, true]) {
      const { host, done } = mountTopBar(context({ runStatus: stalled(3) }), { detailsOpen })
      await nextTick()
      expect(host.querySelector('[data-testid="capacity-stall"]'), `3 ticks, details ${detailsOpen}: the alert`).not.toBeNull()
      done()
    }
  })
})

describe('048 S1 - a lost event stream is said in the state of the network (decision 5; §15 Codex P2)', () => {
  // Red before the fix: with the stream `reconnecting` (a stream that ended normally) and REST still `running`, the
  // state read the same as with an open stream - the loss was visible only in the SSE badge, which is `?debug=1`'s.
  it('a living run whose stream is lost: "no connection" in the one live region; an open or a first connection is not a loss', async () => {
    const runStatus = ref({ run_id: 'run-1', scenario_id: 'sc-1', state: 'running' } as RunStatus)
    const sseState = ref('open')
    const { host, done } = mountTopBar(context({ runStatus, sseState }), { detailsOpen: false })
    await nextTick()
    const state = () => host.querySelector<HTMLElement>('[data-testid="network-state"]')!
    const lost = t('topBar.noConnection')
    const open = state().textContent!.trim()
    expect(open, 'counter-check: an open stream of a running run is not "no connection"').not.toBe(lost)

    sseState.value = 'reconnecting'
    await nextTick()
    expect(state().textContent!.trim()).toBe(lost)
    // Said once: in the one live region of the bar (the first row's state), nowhere else.
    const regions = [...host.querySelectorAll('[aria-live]')].filter((el) => (el.textContent ?? '').includes(lost))
    expect(regions, 'one live region carries it').toHaveLength(1)
    expect(regions[0]).toBe(state())

    sseState.value = 'closed'
    await nextTick()
    expect(state().textContent!.trim(), 'closed under a running run: still lost').toBe(lost)

    sseState.value = 'connecting'
    await nextTick()
    expect(state().textContent!.trim(), 'a (re)connection in progress is not shown as a loss').toBe(open)

    sseState.value = 'reconnecting'
    runStatus.value = { run_id: 'run-1', scenario_id: 'sc-1', state: 'paused' } as RunStatus
    await nextTick()
    expect(state().textContent!.trim(), 'a paused run is living too').toBe(lost)

    runStatus.value = { run_id: 'run-1', scenario_id: 'sc-1', state: 'stopped' } as RunStatus
    sseState.value = 'closed'
    await nextTick()
    expect(state().textContent!.trim(), 'a stopped run closes its stream: that is the stop, not a loss').not.toBe(lost)
    done()
  })
})

describe('048 S1 - the SSE badge is the developer\'s (decision 5)', () => {
  it('rendered with debug only', async () => {
    for (const debug of [false, true]) {
      const { host, done } = mountTopBar(context(), { debug })
      await nextTick()
      expect(host.querySelector('[data-testid="top-details-row"]'), 'the service row is there').not.toBeNull()
      expect(!!host.querySelector('[data-testid="sse-badge"]'), `debug ${debug}`).toBe(debug)
      done()
    }
  })
})
