/**
 * 048 S1 (decisions 1, 2, 4 and 5) - the top bar for its three addressees, mounted and used as a person uses it.
 *
 *  - the administrator (an admin token, server source): "Admin ▾" holds the analytics dock's toggle (moved from the
 *    bottom bar), the refresh of the scenario catalogue (moved from the scenario row) and the run files ("Artifacts",
 *    moved from the bottom bar). A visitor - no token, or a participant JWT - has none of them;
 *  - the visitor: the capacity-stall alert is on the first view, only while payments stall;
 *  - the developer (`debug`): the SSE badge.
 *
 * The analytics toggle's own tests moved here from `BottomBar.test.ts` with it (aria-pressed, the click asks the owner).
 * What this does NOT see: the root's half (the dock is neither mounted nor polled without the token -
 * `SimulatorAppRoot.interact.test.ts`), the browser (`e2e/p048-s1-addressees.spec.ts`).
 */
import { createApp, h, nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import type { ArtifactIndexItem, RunStatus } from '../api/simulatorTypes'
import { provideTopBarContext, type TopBarContext } from '../composables/useTopBarContext'
import TopBar from './TopBar.vue'

function context(over: Partial<TopBarContext> = {}): TopBarContext {
  return {
    apiMode: ref('real'), isTestMode: ref(false), uiTheme: ref('saas'),
    loadingScenarios: ref(false), scenarios: ref([{ scenario_id: 'sc-1', label: 'One' }]),
    selectedScenarioId: ref('sc-1'), intensityPercent: ref(30), runId: ref('run-1'),
    runStatus: ref({ run_id: 'run-1', scenario_id: 'sc-1', state: 'running' } as RunStatus), sseState: ref('open'),
    lastError: ref(null),
    runStats: ref({ startedAtMs: 0, attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} }),
    accessToken: ref('admin-token'), adminRuns: ref([]), adminRunsLoading: ref(false), adminLastError: ref(undefined),
    adminCanGetRuns: ref(true), adminCanStopRuns: ref(true), adminCanAttachRun: ref(true), adminCanStopRun: ref(true),
    ...over,
  } as TopBarContext
}

type Files = { runId: string | null; items: readonly ArtifactIndexItem[]; loading: boolean; refresh: () => Promise<void>; download: (n: string) => void }

function mountTopBar(ctx: TopBarContext, props: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const onToggleAnalytics = vi.fn()
  const onRefreshScenarios = vi.fn()
  const app = createApp({
    setup() {
      provideTopBarContext(ctx)
      return () => h(TopBar, { detailsOpen: true, onToggleAnalytics, onRefreshScenarios, ...props })
    },
  })
  app.mount(host)
  return { host, onToggleAnalytics, onRefreshScenarios, done: () => { app.unmount(); host.remove() } }
}

const admin = (host: HTMLElement) => host.querySelector('details[aria-label="Admin controls"]')
const ADMIN_TOOLS = ['[data-testid="analytics-toggle"]', '[data-testid="scenarios-refresh"]', '[data-testid="admin-run-files"]'] as const

describe('048 S1 - the administrator\'s tools are in "Admin ▾", and only there', () => {
  const files = (): Files => ({ runId: 'run-1', items: [], loading: false, refresh: vi.fn(async () => undefined), download: vi.fn() })

  it('reproducer (в), unit half: with an admin token the analytics toggle, the catalogue refresh and the run files are inside Admin', async () => {
    const { host, done } = mountTopBar(context(), { runFiles: files() })
    await nextTick()
    expect(admin(host), 'counter-check: the admin menu is rendered').not.toBeNull()
    for (const sel of ADMIN_TOOLS) {
      const el = host.querySelector(sel)
      expect(el, sel).not.toBeNull()
      expect(el!.closest('details[aria-label="Admin controls"]'), `${sel} is inside Admin`).not.toBeNull()
    }
    done()
  })

  it('reproducer (в), unit half: a visitor - no token, a participant JWT, or the offline source - has none of them', async () => {
    const jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwIn0.c2lnbmF0dXJl'
    for (const [why, over] of [
      ['no token', { accessToken: ref(null) }],
      ['a participant JWT', { accessToken: ref(jwt) }],
      ['the offline source', { apiMode: ref('fixtures') }],
    ] as const) {
      const { host, done } = mountTopBar(context(over as Partial<TopBarContext>), { runFiles: files() })
      await nextTick()
      // Counter-check: the service row is rendered (the same mount with a token has the tools - the test above).
      expect(host.querySelector('[data-testid="top-details-row"]'), `${why}: the service row is there`).not.toBeNull()
      expect(admin(host), `${why}: no Admin`).toBeNull()
      for (const sel of ADMIN_TOOLS) expect(host.querySelector(sel), `${why}: ${sel}`).toBeNull()
      done()
    }
  })

  it('the analytics toggle reports the dock\'s state and asks its owner to flip it (moved from the bottom bar)', async () => {
    for (const open of [false, true]) {
      const { host, onToggleAnalytics, done } = mountTopBar(context(), { analyticsOpen: open })
      await nextTick()
      const btn = host.querySelector<HTMLButtonElement>('[data-testid="analytics-toggle"]')!
      expect(btn.getAttribute('aria-pressed')).toBe(String(open))
      btn.click()
      expect(onToggleAnalytics).toHaveBeenCalledTimes(1)
      done()
    }
  })

  it('the catalogue refresh in Admin emits refresh-scenarios, and is disabled while the catalogue loads', async () => {
    const loadingScenarios = ref(false)
    const { host, onRefreshScenarios, done } = mountTopBar(context({ loadingScenarios }))
    await nextTick()
    const btn = host.querySelector<HTMLButtonElement>('[data-testid="scenarios-refresh"]')!
    btn.click()
    expect(onRefreshScenarios).toHaveBeenCalledTimes(1)
    loadingScenarios.value = true
    await nextTick()
    expect(btn.disabled).toBe(true)
    expect(host.querySelector('.tb-run-controls [data-testid="scenarios-refresh"]'), 'not in the scenario row any more').toBeNull()
    done()
  })

  it('the run files: no number before the index of this run is read ("not read" is not "no files"); a read shows it', async () => {
    const items = ref<readonly ArtifactIndexItem[]>([])
    const f = files()
    // The owner's read replaces the array (`useSimulatorRealMode.ts`, refreshArtifacts).
    f.refresh = vi.fn(async () => { items.value = [] })
    const { host, done } = mountTopBar(context(), { get runFiles() { return { ...f, items: items.value } } })
    await nextTick()
    const count = () => host.querySelector('[data-testid="admin-run-files-count"]')
    expect(count(), 'before any read: no number').toBeNull()
    expect(host.querySelector('[data-testid="admin-run-files"]')!.textContent, 'and no "No artifacts" either').not.toContain('No artifacts')
    host.querySelector<HTMLButtonElement>('[data-testid="admin-run-files"] button[aria-label="Refresh artifacts"]')!.click()
    await vi.waitFor(() => expect(count()?.textContent).toBe('0'))
    expect(f.refresh).toHaveBeenCalledTimes(1)
    done()
  })

  it('the run files: a failed read (the index not replaced) shows no number; a listed file downloads by its name', async () => {
    const items = ref<readonly ArtifactIndexItem[]>([])
    const f = files()
    f.refresh = vi.fn(async () => undefined) // failed: the array is left as it was
    const download = vi.fn()
    const { host, done } = mountTopBar(context(), { get runFiles() { return { ...f, download, items: items.value } } })
    await nextTick()
    host.querySelector<HTMLButtonElement>('[data-testid="admin-run-files"] button[aria-label="Refresh artifacts"]')!.click()
    await nextTick()
    await nextTick()
    expect(host.querySelector('[data-testid="admin-run-files-count"]'), 'a failed read is not "0 files"').toBeNull()

    items.value = [{ name: 'events.ndjson' } as ArtifactIndexItem]
    await nextTick()
    expect(host.querySelector('[data-testid="admin-run-files-count"]')?.textContent, 'a listed index has its number').toBe('1')
    const fileBtn = [...host.querySelectorAll<HTMLButtonElement>('[data-testid="admin-run-files"] button')].find((b) => b.getAttribute('aria-label') !== 'Refresh artifacts')!
    fileBtn.click()
    expect(download).toHaveBeenCalledWith('events.ndjson')
    done()
  })
})

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
