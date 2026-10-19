import { createApp, h, nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { provideTopBarContext, type TopBarContext } from '../composables/useTopBarContext'
import type { RunStatus } from '../api/simulatorTypes'
import TopBar from './TopBar.vue'

function createTopBarContext(overrides: Partial<TopBarContext> = {}): TopBarContext {
  return {
    apiMode: ref('real'),
    isTestMode: ref(false),
    uiTheme: ref('hud'),
    loadingScenarios: ref(false),
    scenarios: ref([{ scenario_id: 'scenario-1', label: 'Scenario 1' }]),
    selectedScenarioId: ref('scenario-1'),
    intensityPercent: ref(50),
    runId: ref('run-1'),
    runStatus: ref(null),
    sseState: ref('open'),
    lastError: ref(null),
    runStats: ref({
      startedAtMs: 0,
      attempts: 0,
      committed: 0,
      rejected: 0,
      errors: 0,
      timeouts: 0,
      rejectedByCode: {},
      errorsByCode: {},
    }),
    accessToken: ref(null),
    ...overrides,
  }
}

async function mountTopBar(overrides: Partial<TopBarContext> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)

  const listeners = {
    'onGo-sandbox': vi.fn(),
    'onGo-auto-run': vi.fn(),
    'onGo-interact': vi.fn(),
    'onSet-ui-theme': vi.fn(),
    'onRefresh-scenarios': vi.fn(),
    'onStart-run': vi.fn(),
    'onPause': vi.fn(),
    'onResume': vi.fn(),
    'onStop': vi.fn(),
    'onApply-intensity': vi.fn(),
    'onUpdate:selected-scenario-id': vi.fn(),
    'onUpdate:intensity-percent': vi.fn(),
  }

  const app = createApp({
    setup() {
      provideTopBarContext(createTopBarContext(overrides))
      // 043 S1: the dropdowns and statuses these tests are about live behind "Подробнее".
      return () => h(TopBar, { ...listeners, detailsOpen: true })
    },
  })

  app.mount(host)
  await nextTick()

  return { host, app }
}

// 048 S2 (amendment 9): the test "moves keyboard-opened focus inside Admin and restores summary focus on Escape" is gone
// with "Admin ▾" (a removed contract: the bar has no menu any more). The focus rules of a HUD menu are
// `useHudDropdownFocus.test.ts`'s; the live region and the error alert stay below.
describe('TopBar dropdown focus contract', () => {
  // 048 S1 (decision 4): the "Run <state>" badge of the service row is gone (the state of the first row says the same);
  // its live region moved to that state, so a change of the run is still announced - and from the first view.
  it('announces run lifecycle changes and exposes the current run error as an alert', async () => {
    const runStatus = ref<RunStatus | null>(null)
    const lastError = ref<string | null>(null)
    const { app, host } = await mountTopBar({ runStatus, lastError })

    expect(host.querySelector('[aria-label="Run state"]'), 'no Run badge').toBeNull()
    const lifecycle = host.querySelector('[data-testid="network-state"]')
    expect(lifecycle?.getAttribute('role')).toBe('status')
    expect(lifecycle?.getAttribute('aria-live')).toBe('polite')
    expect(lifecycle?.getAttribute('aria-atomic')).toBe('true')
    const before = lifecycle?.textContent

    runStatus.value = { run_id: 'run-1', scenario_id: 'scenario-1', state: 'running' }
    lastError.value = 'Run failed to reconnect'
    await nextTick()

    expect(lifecycle?.textContent, 'the region says something else for a running run').not.toBe(before)
    const error = host.querySelector('[aria-label="Error"]')
    expect(error?.getAttribute('role')).toBe('alert')
    expect(error?.getAttribute('aria-atomic')).toBe('true')
    expect(error?.textContent).toContain('Run failed to reconnect')

    app.unmount()
    host.remove()
  })

  it('BACKLOG 037-5 (a): an error line longer than the bar shows keeps its full text reachable (the reason is at the end)', async () => {
    const full = `SCENARIO_TRUSTLINE_REFUSED: trust line ${'a'.repeat(90)}->${'b'.repeat(90)} UAH: refused (reason: trust_line_status_frozen)`
    const { app, host } = await mountTopBar({
      runStatus: ref<RunStatus | null>({ run_id: 'run-1', scenario_id: 'scenario-1', state: 'error' }),
      lastError: ref<string | null>(full),
    })

    const error = host.querySelector('[aria-label="Error"]')
    expect(error?.textContent).toContain('SCENARIO_TRUSTLINE_REFUSED')
    // the visible line is shortened, so the end of the text is not in it...
    expect(error?.textContent).not.toContain('trust_line_status_frozen')
    // ...and the full text is on the element a pointer rests on
    expect(error?.querySelector('[title]')?.getAttribute('title')).toBe(full)

    app.unmount()
    host.remove()
  })

  it('042 S2: the top bar has no run-pipeline choice (no Pipeline row, no sandbox option); 043 S3: no Advanced menu', async () => {
    // 048 S2 (amendment 8): the scenario select and the tempo are the administrator's - mounted with an admin token.
    const { app, host } = await mountTopBar({ accessToken: ref('admin-token') })

    expect(host.querySelector('[aria-label="Run pipeline"]')).toBeNull()
    // 048 S1 (Verification plan, forbidden item 4): by structure, not by words - the selects of the bar are the theme,
    // the language and the scenario, and none of them offers a pipeline (a sandbox option).
    const selects = [...host.querySelectorAll<HTMLSelectElement>('select')]
    expect(selects.map((s) => s.dataset.testid)).toEqual(['theme-select', 'language-select', 'scenario-select'])
    for (const s of selects) expect([...s.options].map((o) => o.value), s.dataset.testid).not.toContain('fixtures')
    // 043 S3 (decision 5): the "Advanced ⚙" menu, which held only the tempo, is gone; the tempo is in the row itself.
    expect(host.querySelector('details[aria-label="Advanced"]')).toBeNull()
    // anti-vacuum: the bar is the real one, with its service row, and still carries the intensity control that stays
    const intensity = host.querySelector('[aria-label="Intensity"] input[aria-label="Intensity percent"]')
    expect(intensity).toBeTruthy()
    expect(intensity!.closest('details'), 'the tempo is not inside a menu').toBeNull()

    app.unmount()
    host.remove()
  })
})
