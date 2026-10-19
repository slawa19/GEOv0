/**
 * 048 S2 (amendments 8, 9, 10 and 12; the owner's rule of 2026-10-19 "крайние случаи в интерфейсе не обрабатывать") -
 * what the service row of the top bar ("Подробнее") holds for each addressee, mounted as the root mounts it.
 *
 *  - a visitor (no token, a participant JWT): the theme and the language; the stop reason when the run stopped with one.
 *    No run row (scenario, Start/Stop, tempo with Apply, "No scenarios…"), no payments badge, no "Admin ▾";
 *  - the administrator (an admin token): the same plus the row of the own run and the payments badge;
 *  - the developer (`debug`): plus the SSE badge and the analytics dock's toggle - with or without a token.
 *
 * The row is read as a list of what is in it - every control (select, button, input, summary, link, a tabbable element)
 * and every passive item named by a test id - by structure, not by words (048, Verification plan, forbidden item 4; the
 * words are 046's). An absence is "not in the DOM" (`v-if`), never a style.
 * What this does NOT see: the bottom bar and the network figures (the slot) - `BottomBar.test.ts` and the browser test
 * `e2e/p048-s2-edge-cases-out.spec.ts`, which reads the whole "Подробнее" of the page.
 */
import { createApp, h, nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import type { RunStatus } from '../api/simulatorTypes'
import { provideTopBarContext, type TopBarContext } from '../composables/useTopBarContext'
import TopBar from './TopBar.vue'

const JWT = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwIn0.c2lnbmF0dXJl'
const running = { run_id: 'run-1', scenario_id: 'sc-1', state: 'running' } as RunStatus
const stopped = { run_id: 'run-1', scenario_id: 'sc-1', state: 'stopped', stop_source: 'server', stop_reason: 'idle_no_subscribers' } as RunStatus

function context(over: Partial<TopBarContext> = {}): TopBarContext {
  return {
    apiMode: ref('real'), isTestMode: ref(false), uiTheme: ref('saas'),
    loadingScenarios: ref(false), scenarios: ref([{ scenario_id: 'sc-1', label: 'One' }]),
    selectedScenarioId: ref('sc-1'), intensityPercent: ref(30), runId: ref('run-1'), runStatus: ref(running), sseState: ref('open'),
    lastError: ref(null),
    runStats: ref({ startedAtMs: 0, attempts: 4, committed: 3, rejected: 1, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} }),
    accessToken: ref(null),
    ...over,
  } as TopBarContext
}

function mountTopBar(ctx: TopBarContext, props: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const onToggleAnalytics = vi.fn()
  const app = createApp({ setup() { provideTopBarContext(ctx); return () => h(TopBar, { detailsOpen: true, onToggleAnalytics, ...props }) } })
  app.mount(host)
  return { host, onToggleAnalytics, done: () => { app.unmount(); host.remove() } }
}

/** What the service row holds, in document order: a control by its test id, aria-label or text; a passive item by its test id. */
function rowContents(host: HTMLElement): string[] {
  const row = host.querySelector<HTMLElement>('[data-testid="top-details-row"]')
  if (!row) return ['<no service row>']
  const passive = ['stop-reason', 'run-payments', 'sse-badge']
  const sel = 'select, button, input, summary, a[href], [tabindex]:not([tabindex="-1"]), ' + passive.map((id) => `[data-testid="${id}"]`).join(', ')
  return [...row.querySelectorAll<HTMLElement>(sel)].map((el) =>
    el.dataset.testid ?? el.getAttribute('aria-label') ?? (el.textContent ?? '').replace(/\s+/g, ' ').trim())
}

const VISITOR = ['theme-select', 'language-select']
// 046 wave 1, full (2026-10-19): Start, Stop, the tempo and Apply carry test ids now (`rowContents` reads the test id
// first) - their words and accessible names are the dictionary's, in the language of the interface.
const OWN_RUN_ROW_RUNNING = ['scenario-select', 'run-stop', 'intensity-input', 'intensity-apply']

describe('048 S2 - the service row of the top bar, by addressee (amendment 12)', () => {
  it('a visitor - no token, or a participant JWT: the theme and the language; nothing of the run but its stop reason', async () => {
    for (const [why, token] of [['no token', null], ['a participant JWT', JWT]] as const) {
      for (const [state, status, extra] of [['running', running, []], ['stopped with a reason', stopped, ['stop-reason']]] as const) {
        const { host, done } = mountTopBar(context({ accessToken: ref(token), runStatus: ref(status) }))
        await nextTick()
        expect(rowContents(host), `${why}, ${state}`).toEqual([...VISITOR, ...extra])
        expect(host.querySelector('details'), `${why}, ${state}: no menu ("Admin ▾" is gone)`).toBeNull()
        done()
      }
    }
  })

  it('the administrator (an admin token): the same plus the row of the own run and the payments badge', async () => {
    const live = mountTopBar(context({ accessToken: ref('admin-token') }))
    await nextTick()
    expect(rowContents(live.host)).toEqual([...VISITOR, ...OWN_RUN_ROW_RUNNING, 'run-payments'])
    expect(live.host.querySelector('details'), 'no menu ("Admin ▾" is gone) for the administrator either').toBeNull()
    live.done()

    // A stopped run: Start instead of Stop, Apply disabled, the stop reason (passive, as for everyone) and the badge.
    const ended = mountTopBar(context({ accessToken: ref('admin-token'), runStatus: ref(stopped) }))
    await nextTick()
    expect(rowContents(ended.host)).toEqual([...VISITOR, 'scenario-select', 'run-start', 'intensity-input', 'intensity-apply', 'stop-reason', 'run-payments'])
    ended.done()
  })

  it('"No scenarios…" is the administrator\'s line too: a visitor with an empty catalogue gets none (counter-check: the token gets it)', async () => {
    for (const [token, expected] of [[null, 0], ['admin-token', 1]] as const) {
      const { host, done } = mountTopBar(context({ accessToken: ref(token), scenarios: ref([]), runId: ref(null), runStatus: ref(null) }))
      await nextTick()
      expect(host.querySelectorAll('[data-testid="no-scenarios"]').length, `token ${token}`).toBe(expected)
      done()
    }
  })

  // Internal adversarial pass (2026-10-19): a visitor's running run without `debug` leaves the status cluster empty, and
  // an empty element with a name is an axe violation (aria-prohibited-attr). Red before the fix: the cluster was
  // always named "Status".
  it('the status cluster is named only while it holds something (an empty named div is an axe violation)', async () => {
    for (const [why, over, props, named] of [
      ['a visitor, running, no debug', { accessToken: ref(null) }, {}, false],
      ['the offline source', { apiMode: ref('fixtures') }, {}, false],
      ['a visitor, stopped with a reason', { accessToken: ref(null), runStatus: ref(stopped) }, {}, true],
      ['a visitor with debug', { accessToken: ref(null) }, { debug: true }, true],
      ['the administrator (the payments badge)', { accessToken: ref('admin-token') }, {}, true],
    ] as const) {
      const { host, done } = mountTopBar(context(over as Partial<TopBarContext>), props)
      await nextTick()
      const cluster = host.querySelector<HTMLElement>('[data-testid="top-details-row"] > .hud-bar__right')!
      expect(cluster.getAttribute('aria-label'), `${why}: the name`).toBe(named ? 'Status' : null)
      expect((cluster.textContent ?? '').trim().length > 0, `${why}: the cluster holds text exactly when named`).toBe(named)
      done()
    }
  })

  it('the offline source: no run row and no payments badge, whatever the token (there is no run to control)', async () => {
    const { host, done } = mountTopBar(context({ accessToken: ref('admin-token'), apiMode: ref('fixtures') }))
    await nextTick()
    expect(rowContents(host)).toEqual(VISITOR)
    done()
  })
})

describe('048 S2 - the analytics dock\'s toggle is the developer\'s (amendment 10)', () => {
  it('with `debug` only - for a visitor and for the administrator alike; with it the SSE badge (S1)', async () => {
    for (const token of [null, 'admin-token']) {
      for (const debug of [false, true]) {
        const { host, done } = mountTopBar(context({ accessToken: ref(token) }), { debug })
        await nextTick()
        const contents = rowContents(host)
        expect(contents.includes('analytics-toggle'), `token ${token}, debug ${debug}: the toggle`).toBe(debug)
        expect(contents.includes('sse-badge'), `token ${token}, debug ${debug}: the SSE badge`).toBe(debug)
        done()
      }
    }
  })

  it('reports the dock\'s state and asks its owner to flip it', async () => {
    for (const open of [false, true]) {
      const { host, onToggleAnalytics, done } = mountTopBar(context(), { debug: true, analyticsOpen: open })
      await nextTick()
      const btn = host.querySelector<HTMLButtonElement>('[data-testid="analytics-toggle"]')!
      expect(btn.getAttribute('aria-pressed')).toBe(String(open))
      btn.click()
      expect(onToggleAnalytics).toHaveBeenCalledTimes(1)
      done()
    }
  })
})
