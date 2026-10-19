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
    accessToken: ref('admin-token'), adminRuns: ref([]), adminRunsLoading: ref(false), adminLastError: ref(undefined),
    adminCanGetRuns: ref(true), adminCanStopRuns: ref(true), adminCanAttachRun: ref(true), adminCanStopRun: ref(true),
    ...over,
  } as TopBarContext
}

type Files = { runId: string | null; items: readonly ArtifactIndexItem[]; loading: boolean; refresh: () => Promise<readonly ArtifactIndexItem[] | null>; download: (n: string) => void }

/** The owner's successful read (`useSimulatorRealMode.ts` `refreshArtifacts`): it installs a new array and resolves with it. */
const installs = (items: { value: readonly ArtifactIndexItem[] }, next: ArtifactIndexItem[] = []) => async () => {
  items.value = next
  return next
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

const admin = (host: HTMLElement) => host.querySelector('details[aria-label="Admin controls"]')
const ADMIN_TOOLS = ['[data-testid="analytics-toggle"]', '[data-testid="admin-run-files"]'] as const

describe('048 S1 - the administrator\'s tools are in "Admin ▾", and only there', () => {
  const files = (): Files => ({ runId: 'run-1', items: [], loading: false, refresh: vi.fn(async () => null), download: vi.fn() })

  it('reproducer (в), unit half: with an admin token the analytics toggle and the run files are inside Admin', async () => {
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

  // The owner's word (2026-10-19, a rule): the refresh of the scenario catalogue is an edge case the interface ignores -
  // the catalogue is read when the page loads; after an admin uploads a scenario, a reload picks it up. Replaces the test
  // "the catalogue refresh in Admin emits refresh-scenarios…" (its contract is removed). By structure: the tools row of
  // Admin holds the analytics toggle alone, and the scenario row has one button besides the run's (no icon button).
  // Red on `fb818563`: the tools row held two buttons (Analytics and ↻).
  it('no refresh of the scenario catalogue anywhere: Admin\'s tools row holds the analytics toggle alone', async () => {
    const { host, done } = mountTopBar(context({ runId: ref(null), runStatus: ref(null) }), { runFiles: files() })
    await nextTick()
    expect(admin(host), 'counter-check: Admin is rendered').not.toBeNull()
    const tools = [...host.querySelectorAll<HTMLElement>('[data-testid="admin-tools"] button')].map((b) => b.dataset.testid ?? b.getAttribute('aria-label'))
    expect(tools).toEqual(['analytics-toggle'])
    // The scenario row: the select and Start (no run) - and no other button.
    const runRow = [...host.querySelectorAll<HTMLElement>('.tb-run-controls > button')]
    expect(runRow.length, 'Start only (no run is live), no refresh beside the scenario').toBe(1)
    done()
  })

  it('the run files: no number before the index of this run is read ("not read" is not "no files"); a read shows it', async () => {
    const items = ref<readonly ArtifactIndexItem[]>([])
    const f = files()
    // The owner's read replaces the array (`useSimulatorRealMode.ts`, refreshArtifacts).
    f.refresh = vi.fn(installs(items))
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

  // §15 Codex P2 (2026-10-18): the "read" marker follows the index, not the run id. Every reset of the index by its
  // owner (`useSimulatorRealMode.ts`: a start, an attach - also of the same run again -, a stale run) assigns a new
  // empty array. Red before the fix: after attaching run A again, and after A -> B -> A, the panel said "0" and
  // "No artifacts" for an index nobody had read.
  it('the run files: attaching the same run again, or A -> B -> A, is "not read" again until the index is read', async () => {
    const runId = ref<string | null>('run-A')
    const items = ref<readonly ArtifactIndexItem[]>([])
    const f = files()
    f.refresh = vi.fn(installs(items)) // a successful read of an empty index installs a new array
    const { host, done } = mountTopBar(context(), { get runFiles() { return { ...f, runId: runId.value, items: items.value } } })
    await nextTick()
    const count = () => host.querySelector('[data-testid="admin-run-files-count"]')
    const read = async () => {
      host.querySelector<HTMLButtonElement>('[data-testid="admin-run-files"] button[aria-label="Refresh artifacts"]')!.click()
      await vi.waitFor(() => expect(count()?.textContent, 'read: the number').toBe('0'))
    }
    await read()

    // Attach A again: the owner resets the index (a new empty array), the run id is the same.
    items.value = []
    await nextTick()
    expect(count(), 'attached A again: not read').toBeNull()
    expect(host.querySelector('[data-testid="admin-run-files"]')!.textContent, 'and no "No artifacts"').not.toContain('No artifacts')
    await read()

    // A -> B -> A without reading B: each attach resets the index.
    runId.value = 'run-B'
    items.value = []
    await nextTick()
    expect(count(), 'B, not read').toBeNull()
    runId.value = 'run-A'
    items.value = []
    await nextTick()
    expect(count(), 'back to A, not read').toBeNull()
    done()
  })

  // §15 Codex fix-delta P2 (2026-10-18): a read in flight, then Attach of the same run (the owner resets the index to a
  // new empty array), then the read FAILS. The panel inferred "read" from "same run, another array after the await" and
  // remembered the reset array: "0 / No artifacts" for an index nobody read. Now only the owner's success counts.
  it('the run files: a read in flight, then Attach of the same run, then the read fails - "not read"; a later successful read - "read"', async () => {
    const items = ref<readonly ArtifactIndexItem[]>([])
    const f = files()
    let fail!: () => void
    // The panel calls what the owner gives it at render; the owner's behaviour is switched under it.
    let impl: () => Promise<readonly ArtifactIndexItem[] | null> = () =>
      new Promise((resolve) => { fail = () => resolve(null) })
    f.refresh = vi.fn(() => impl())
    const { host, done } = mountTopBar(context(), { get runFiles() { return { ...f, items: items.value } } })
    await nextTick()
    const count = () => host.querySelector('[data-testid="admin-run-files-count"]')
    host.querySelector<HTMLButtonElement>('[data-testid="admin-run-files"] button[aria-label="Refresh artifacts"]')!.click()
    await nextTick()
    items.value = [] // Attach of the same run: the owner resets the index
    await nextTick()
    fail()
    // Let the panel finish whatever it does after the read (its handler awaits the read and a render).
    for (let i = 0; i < 5; i += 1) await new Promise((r) => setTimeout(r, 0))
    expect(count(), 'a failed read across an Attach is not a read').toBeNull()
    expect(host.querySelector('[data-testid="admin-run-files"]')!.textContent, 'and no "No artifacts"').not.toContain('No artifacts')

    impl = installs(items)
    host.querySelector<HTMLButtonElement>('[data-testid="admin-run-files"] button[aria-label="Refresh artifacts"]')!.click()
    await vi.waitFor(() => expect(count()?.textContent, 'a successful read after the Attach').toBe('0'))
    done()
  })

  it('the run files: a failed read (the index not replaced) shows no number; a listed file downloads by its name', async () => {
    const items = ref<readonly ArtifactIndexItem[]>([])
    const f = files()
    f.refresh = vi.fn(async () => null) // failed: the array is left as it was, the owner says null
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
