/**
 * TopBar wiring, observed (F-041-9, converted in 043 S1). Until then this file read `TopBar.vue` and
 * `SimulatorAppRoot.vue` as strings and looked for `emit('start-run'`, `@start-run="realActions.startRun"`, CSS
 * literals and the like: it held the spelling of the source, not what a click does, and a layout change that kept
 * every behaviour (043 S1 moved the controls behind "Подробнее") turned it red.
 *
 * Now: TopBar is mounted with its context, each control is used the way a person uses it, and the event it emits -
 * name and payload - is what the test reads. The parent's half (each event reaches the app's action) is observed in
 * the mounted root: `SimulatorAppRoot.interact.test.ts`, "wiring (was a source-text guard)". The dropdown keyboard
 * contract (focus in, Tab trapped, Escape restores) is `TopBar.focus.test.ts`; the CSS literals of the old file are
 * not behaviour and are not re-checked.
 */
import { createApp, h, nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import type { RunStatus } from '../api/simulatorTypes'
import { provideTopBarContext, type TopBarContext } from '../composables/useTopBarContext'
import TopBar from './TopBar.vue'

const EVENTS = [
  'toggle-details', 'update:selected-scenario-id', 'update:intensity-percent',
  'set-ui-theme', 'refresh-scenarios', 'start-run', 'pause', 'resume', 'stop', 'apply-intensity',
  'admin-get-runs', 'admin-stop-runs', 'admin-attach-run', 'admin-stop-run', 'toggle-analytics',
] as const

function context(over: Partial<TopBarContext> = {}): TopBarContext {
  return {
    apiMode: ref('real'), isTestMode: ref(false), uiTheme: ref('saas'),
    loadingScenarios: ref(false), scenarios: ref([{ scenario_id: 'sc-1', label: 'Riverside town' }, { scenario_id: 'sc-2', label: 'Two' }]),
    selectedScenarioId: ref('sc-1'), intensityPercent: ref(30), runId: ref('run-1'), runStatus: ref(null), sseState: ref('open'),
    lastError: ref(null),
    runStats: ref({ startedAtMs: 0, attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} }),
    accessToken: ref('admin-token'), adminRuns: ref([{ run_id: 'run-9', state: 'running', scenario_id: 'sc-1', owner_id: 'o' }]),
    adminRunsLoading: ref(false), adminLastError: ref(undefined),
    adminCanGetRuns: ref(true), adminCanStopRuns: ref(true), adminCanAttachRun: ref(true), adminCanStopRun: ref(true),
    ...over,
  } as TopBarContext
}

function mountTopBar(ctx: TopBarContext, detailsOpen: boolean) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const spies = Object.fromEntries(EVENTS.map((e) => [e, vi.fn()])) as Record<(typeof EVENTS)[number], ReturnType<typeof vi.fn>>
  // Listener props by the kebab-case name of each event, exactly as the root's template binds them.
  const listeners = Object.fromEntries(EVENTS.map((e) => [`on${e[0]!.toUpperCase()}${e.slice(1)}`, spies[e]]))
  const app = createApp({ setup() { provideTopBarContext(ctx); return () => h(TopBar, { ...listeners, detailsOpen }) } })
  app.mount(host)
  return { host, spies, done: () => { app.unmount(); host.remove() } }
}

const button = (host: HTMLElement, text: string) =>
  [...host.querySelectorAll<HTMLButtonElement>('button')].find((b) => (b.textContent ?? '').replace(/\s+/g, ' ').trim() === text)

describe('TopBar wiring - what each control emits', () => {
  it('the toggle emits toggle-details and states whether it is open', async () => {
    const off = mountTopBar(context(), false)
    await nextTick()
    const t = off.host.querySelector<HTMLButtonElement>('[data-testid="details-toggle"]')!
    expect(t.getAttribute('aria-expanded')).toBe('false')
    t.click()
    expect(off.spies['toggle-details']).toHaveBeenCalledTimes(1)
    off.done()

    const on = mountTopBar(context(), true)
    await nextTick()
    expect(on.host.querySelector('[data-testid="details-toggle"]')!.getAttribute('aria-expanded')).toBe('true')
    on.done()
  })

  it('selects and inputs emit update events with the value (the intensity clamped to 0..100)', async () => {
    // No run: the scenario select is enabled only then (BACKLOG 043-3, 046 wave 1 - `TopBar.p046w1.test.ts`).
    const { host, spies, done } = mountTopBar(context({ runId: ref(null) }), true)
    await nextTick()
    const scenario = host.querySelector<HTMLSelectElement>('select[aria-label="Scenario"]')!
    scenario.value = 'sc-2'
    scenario.dispatchEvent(new Event('change'))
    expect(spies['update:selected-scenario-id']).toHaveBeenCalledWith('sc-2')

    const intensity = host.querySelector<HTMLInputElement>('input[aria-label="Intensity percent"]')!
    intensity.value = '-5'
    intensity.dispatchEvent(new Event('input'))
    intensity.value = '42.4'
    intensity.dispatchEvent(new Event('input'))
    expect(spies['update:intensity-percent'].mock.calls).toEqual([[0], [42]])

    const theme = host.querySelector<HTMLSelectElement>('select[aria-label="UI theme"]')!
    theme.value = 'hud'
    theme.dispatchEvent(new Event('change'))
    expect(spies['set-ui-theme']).toHaveBeenCalledWith('hud')
    done()
  })

  /**
   * 048 S1 (decision 4): the service row had its own Pause/Resume beside "Поток" - two controls of one state. They are
   * gone; "Поток" pauses a living run and resumes a paused one (rewritten here from "Pause"/"Resume", not deleted).
   * Observed by behaviour, not by a button's words: EVERY button of the service row is clicked, in both states of the
   * run, and none of them emits `pause` or `resume` - red before S1 (the row's Pause emitted `pause`, its Resume
   * `resume`). The refresh of the scenario catalogue is in "Admin ▾" (decision 5) and still emits its event.
   */
  it('buttons emit their events: run lifecycle and intensity; "Поток" alone pauses and resumes (043 S2: no mode segment)', async () => {
    const runStatus = ref<RunStatus | null>({ run_id: 'run-1', scenario_id: 'sc-1', state: 'running' } as RunStatus)
    const { host, spies, done } = mountTopBar(context({ runStatus }), true)
    await nextTick()
    for (const gone of ['Sandbox', 'Auto-Run', 'Interact']) expect(button(host, gone), `${gone}: the mode segment is gone`).toBeUndefined()

    const row = host.querySelector<HTMLElement>('[data-testid="top-details-row"]')!
    const runButtons = () => [...row.querySelectorAll<HTMLButtonElement>('.tb-run-controls button')]
    for (const state of ['running', 'paused'] as const) {
      runStatus.value = { run_id: 'run-1', scenario_id: 'sc-1', state } as RunStatus
      await nextTick()
      // Counter-check: the run controls are rendered (Stop and Apply at least) - the clicks below reach something.
      expect(runButtons().length, `${state}: the run controls are there`).toBeGreaterThanOrEqual(2)
      for (const b of [...row.querySelectorAll<HTMLButtonElement>('button')]) b.click()
      expect([spies.pause.mock.calls.length, spies.resume.mock.calls.length], `${state}: no button of the service row pauses or resumes`).toEqual([0, 0])
    }
    expect(spies.stop, 'Stop of the run controls').toHaveBeenCalled()
    expect(spies['apply-intensity'], 'Apply of the tempo').toHaveBeenCalled()

    // The flow button of the first row is the one that does.
    runStatus.value = { run_id: 'run-1', scenario_id: 'sc-1', state: 'running' } as RunStatus
    await nextTick()
    host.querySelector<HTMLButtonElement>('[data-testid="flow-toggle"]')!.click()
    expect(spies.pause).toHaveBeenCalledTimes(1)
    runStatus.value = { run_id: 'run-1', scenario_id: 'sc-1', state: 'paused' } as RunStatus
    await nextTick()
    host.querySelector<HTMLButtonElement>('[data-testid="flow-toggle"]')!.click()
    expect(spies.resume).toHaveBeenCalledTimes(1)

    host.querySelector<HTMLButtonElement>('[data-testid="scenarios-refresh"]')!.click()
    expect(spies['refresh-scenarios'], 'the catalogue refresh, in Admin').toHaveBeenCalled()
    expect(row.querySelector('[data-testid="scenarios-refresh"]')?.closest('details[aria-label="Admin controls"]'), 'it lives in Admin').not.toBeNull()
    done()
  })

  it('Start emits start-run when no run is active', async () => {
    const { host, spies, done } = mountTopBar(context({ runId: ref(null) }), true)
    await nextTick()
    button(host, 'Start')!.click()
    expect(spies['start-run']).toHaveBeenCalledTimes(1)
    done()
  })

  it('the Admin menu emits the admin events, with the run id for the per-run ones', async () => {
    const { host, spies, done } = mountTopBar(context(), true)
    await nextTick()
    host.querySelector<HTMLButtonElement>('button[aria-label="Refresh runs list"]')!.click()
    expect(spies['admin-get-runs']).toHaveBeenCalledTimes(1)
    host.querySelector<HTMLButtonElement>('button[aria-label="Stop all runs"]')!.click()
    expect(spies['admin-stop-runs']).toHaveBeenCalledTimes(1)
    host.querySelector<HTMLButtonElement>('button[aria-label="Attach to selected run"]')!.click()
    expect(spies['admin-attach-run']).toHaveBeenCalledWith('run-9')
    host.querySelector<HTMLButtonElement>('button[aria-label="Stop selected run"]')!.click()
    expect(spies['admin-stop-run']).toHaveBeenCalledWith('run-9')
    done()
  })

  it('with Details off none of those controls is rendered, so none can emit (043 S1)', async () => {
    const { host, spies, done } = mountTopBar(context({ runStatus: ref({ run_id: 'run-1', state: 'running' } as RunStatus) }), false)
    await nextTick()
    expect(host.querySelectorAll('select, input').length, 'no select or input in the first row').toBe(0)
    const texts = [...host.querySelectorAll('button')].map((b) => (b.textContent ?? '').replace(/\s+/g, ' ').trim())
    // 043 S2: the flow button of a living run, and the toggle (the actions come from the root, slot "actions").
    expect(texts, 'the first row: the flow button and the toggle').toEqual(['⏸ Поток', 'Подробнее ▾'])
    expect(host.querySelector('summary'), 'no menu').toBeNull()
    for (const s of Object.values(spies)) expect(s).not.toHaveBeenCalled()
    done()
  })

  it('043 S2: the flow button - pause on a living run, resume on a paused one, absent otherwise', async () => {
    const runStatus = ref<RunStatus | null>({ run_id: 'run-1', scenario_id: 'sc-1', state: 'running' } as RunStatus)
    const apiMode = ref<'real' | 'fixtures'>('real')
    const { host, spies, done } = mountTopBar(context({ runStatus, apiMode }), false)
    const flow = () => host.querySelector<HTMLButtonElement>('[data-testid="flow-toggle"]')
    await nextTick()
    expect(flow()!.getAttribute('aria-pressed')).toBe('true')
    flow()!.click()
    expect([spies.pause.mock.calls.length, spies.resume.mock.calls.length]).toEqual([1, 0])

    runStatus.value = { run_id: 'run-1', scenario_id: 'sc-1', state: 'paused' } as RunStatus
    await nextTick()
    expect(flow()!.getAttribute('aria-pressed')).toBe('false')
    flow()!.click()
    expect([spies.pause.mock.calls.length, spies.resume.mock.calls.length]).toEqual([1, 1])

    for (const state of ['created', 'stopping', 'stopped', 'error']) {
      runStatus.value = { run_id: 'run-1', scenario_id: 'sc-1', state } as RunStatus
      await nextTick()
      expect(flow(), `${state}: nothing to pause or resume`).toBeNull()
    }
    runStatus.value = null
    await nextTick()
    expect(flow(), 'no run').toBeNull()
    runStatus.value = { run_id: 'run-1', scenario_id: 'sc-1', state: 'running' } as RunStatus
    apiMode.value = 'fixtures'
    await nextTick()
    expect(flow(), 'the offline snapshot has no run').toBeNull()
    done()
  })

  it('043 S2 (review B1): the network is named by the scenario NAME the API gives (`name`; `label` is a UI-only field)', async () => {
    const scenarios = ref([{ scenario_id: 'riverside-town-50', name: 'Riverside town' }, { scenario_id: 'other', name: '  ' }])
    const selectedScenarioId = ref('riverside-town-50')
    const { host, done } = mountTopBar(context({ scenarios, selectedScenarioId, runStatus: ref(null) } as Partial<TopBarContext>), true)
    await nextTick()
    expect(host.querySelector('[data-testid="network-name"]')!.textContent, 'the name, not the slug').toContain('Riverside town')
    const options = [...host.querySelectorAll<HTMLOptionElement>('select[aria-label="Scenario"] option')].map((o) => o.textContent!.trim())
    expect(options, 'the selector names the scenario too; a blank name falls back to the id').toEqual(['— select —', 'Riverside town (riverside-town-50)', 'other'])
    selectedScenarioId.value = 'other'
    await nextTick()
    expect(host.querySelector('[data-testid="network-name"]')!.textContent, 'a blank name: the id').toContain('other')
    done()
  })
})
