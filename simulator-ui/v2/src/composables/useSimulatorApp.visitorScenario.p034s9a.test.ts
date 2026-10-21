/**
 * 034 S9-A (`T3485.1`, the owner's answer П-S9-1 (а), 2026-10-21): the network a person opens when nothing is stored is
 * `clearing-demo-10` if the catalogue offers it, else the catalogue's first scenario - ONE rule
 * (`pickDefaultScenarioId`, `useInteractAutoBootstrapRun.ts`) for the catalogue load and the opening's autostart.
 *
 * On the real composable: the API functions are mocked at the module boundary (as `useSimulatorApp.autostart.p043.test.ts`);
 * what is observed is the run the opening creates (`createRun`'s `scenario_id`) and the stored choice.
 *
 * Red before S9-A: with nothing stored the catalogue load picked `greenfield-village-100-realistic-v2`
 * (`DEFAULT_REAL_SCENARIO_ID`), and the autostart's own preference for `clearing-demo-10` was never reached.
 */
import { effectScope, nextTick, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { connectSse } from '../api/sse'
import { createRun, getActiveRun, getRun, getScenarioPreview, getSnapshot, listScenarios } from '../api/simulatorApi'
import type { SimulatorGraphSnapshot } from '../api/simulatorTypes'
import { useSimulatorApp } from './useSimulatorApp'

vi.mock('../api/simulatorApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulatorApi')>()
  return {
    ...actual,
    listScenarios: vi.fn(),
    getActiveRun: vi.fn(),
    getRun: vi.fn(),
    getSnapshot: vi.fn(),
    getScenarioPreview: vi.fn(),
    createRun: vi.fn(),
    ensureSession: vi.fn(async () => ({ actor_kind: 'anonymous', owner_id: 'o1' })),
  }
})
vi.mock('../api/equivalentsApi', () => ({ fetchEquivalentPrecisions: vi.fn(async () => []) }))
vi.mock('../api/sse', () => ({
  connectSse: vi.fn(
    (opts: { signal?: AbortSignal }) =>
      new Promise<void>((_resolve, reject) => {
        const abort = () => reject(new DOMException('The operation was aborted.', 'AbortError'))
        if (opts.signal?.aborted) return abort()
        opts.signal?.addEventListener('abort', abort, { once: true })
      }),
  ),
}))

const GREENFIELD = 'greenfield-village-100-realistic-v2'
const RIVERSIDE = 'riverside-town-50-realistic-v2'
const CLEARING_DEMO = 'clearing-demo-10'
/** The catalogue of the plan's reproducer (§7.1): the village first, the small network last. */
const FULL_CATALOGUE = [GREENFIELD, RIVERSIDE, CLEARING_DEMO]

function snap(): SimulatorGraphSnapshot {
  return {
    equivalent: 'UAH',
    generated_at: '2026-10-21T00:00:00Z',
    nodes: [{ id: 'A', name: 'A', type: 'person', status: 'active' }, { id: 'B', name: 'B', type: 'person', status: 'active' }],
    links: [{ source: 'A', target: 'B', trust_limit: '100', used: '0', available: '100', status: 'active' }],
  } as unknown as SimulatorGraphSnapshot
}

function catalogue(ids: string[]) {
  vi.mocked(listScenarios).mockResolvedValue({
    api_version: 'simulator-api/1',
    items: ids.map((scenario_id) => ({ scenario_id, label: scenario_id })),
  } as never)
}

async function settle(): Promise<void> {
  for (let round = 0; round < 3; round += 1) {
    for (let i = 0; i < 20; i += 1) {
      await Promise.resolve()
      await nextTick()
    }
    await vi.advanceTimersByTimeAsync(200)
  }
}

let scope: EffectScope | null = null
function boot() {
  scope = effectScope()
  return scope.run(() => useSimulatorApp())!
}

/** The scenario of the run the opening created. */
function startedScenario(): string | undefined {
  expect(createRun, 'the opening creates one run').toHaveBeenCalledTimes(1)
  return (vi.mocked(createRun).mock.calls[0]![1] as { scenario_id?: string }).scenario_id
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
  vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  for (const m of [listScenarios, getActiveRun, getRun, getSnapshot, getScenarioPreview, createRun]) vi.mocked(m).mockReset()
  vi.mocked(connectSse).mockClear()
  catalogue(FULL_CATALOGUE)
  vi.mocked(getActiveRun).mockResolvedValue({ run_id: null } as never)
  vi.mocked(getRun).mockImplementation((async (_c: unknown, id: string) => ({
    run_id: id, scenario_id: 'x', state: 'running', api_version: 'simulator-api/1', mode: 'real', sim_time_ms: 0,
    intensity_percent: 5, ops_sec: 0, queue_depth: 0, last_event_type: null, current_phase: null, last_error: null,
  })) as never)
  vi.mocked(getSnapshot).mockImplementation((async () => snap()) as never)
  vi.mocked(getScenarioPreview).mockImplementation((async () => snap()) as never)
  vi.mocked(createRun).mockImplementation((async () => ({ run_id: 'R_NEW' })) as never)
  window.history.replaceState({}, '', '/')
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  scope?.stop()
  scope = null
  window.history.replaceState({}, '', '/')
  window.localStorage.clear()
})

describe('034 S9-A: one rule picks the network a person opens', () => {
  it('nothing stored: the opening starts `clearing-demo-10` (the catalogue offers it, though not first)', async () => {
    const app = boot()
    await settle()
    expect(app.real.selectedScenarioId, 'the catalogue load picks the small network').toBe(CLEARING_DEMO)
    expect(startedScenario(), 'the run the opening creates').toBe(CLEARING_DEMO)
    expect(window.localStorage.getItem('geo.sim.v2.selectedScenarioId'), 'the pick is stored like any choice').toBe(CLEARING_DEMO)
  })

  it('counter-check: a stored choice the catalogue offers wins (the village stays the village)', async () => {
    window.localStorage.setItem('geo.sim.v2.selectedScenarioId', GREENFIELD)
    const app = boot()
    await settle()
    expect(app.real.selectedScenarioId).toBe(GREENFIELD)
    expect(startedScenario()).toBe(GREENFIELD)
  })

  it('counter-check: a stored choice the catalogue no longer offers falls back to the rule (`clearing-demo-10`)', async () => {
    window.localStorage.setItem('geo.sim.v2.selectedScenarioId', 'removed-scenario')
    boot()
    await settle()
    expect(startedScenario()).toBe(CLEARING_DEMO)
  })

  it('counter-check: a catalogue without `clearing-demo-10` (the operator narrowed it) - the catalogue\'s first scenario', async () => {
    catalogue([RIVERSIDE, GREENFIELD])
    const app = boot()
    await settle()
    expect(app.real.selectedScenarioId).toBe(RIVERSIDE)
    expect(startedScenario()).toBe(RIVERSIDE)
  })

  it('the administrator\'s choice under «Подробнее» is kept: chosen, stored, and opened again after a reload', async () => {
    const first = boot()
    await settle()
    expect(first.real.selectedScenarioId).toBe(CLEARING_DEMO)
    first.realActions.setSelectedScenarioId(RIVERSIDE)
    await settle()
    expect(window.localStorage.getItem('geo.sim.v2.selectedScenarioId')).toBe(RIVERSIDE)

    scope?.stop()
    window.localStorage.removeItem('geo.sim.v2.runId') // a fresh opening; only the choice stays stored
    vi.mocked(createRun).mockClear()
    const again = boot()
    await settle()
    expect(again.real.selectedScenarioId, 'the rule does not override a stored choice').toBe(RIVERSIDE)
    expect(startedScenario()).toBe(RIVERSIDE)
  })
})
