/**
 * 042 S2: the run mode is no longer a client setting. The hidden Advanced > Pipeline choice ("real (DB)" /
 * "sandbox (topology-only)") and the `desiredMode` state behind it are gone; every run creation sends `mode: 'real'`
 * and the preview source says `mode=real`.
 *
 * The cases go through the REAL entrance (`useSimulatorApp`: boot, `realActions.startRun`), with the transport
 * functions of `simulatorApi` mocked at the module boundary - `createRun` is where a mode would leave the client.
 *
 * What this does NOT show: the wire query string of the preview request (that is
 * `simulatorApi.contract.test.ts`, "042 S2: the preview request asks for the real run mode"), and the server side
 * (stage 1). The backend-less demo (`?mode=fixtures`) is another thing and is not exercised here.
 */
import { effectScope, nextTick, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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

const MODE_KEY = 'geo.sim.v2.desiredMode'

function snap(nodeId: string): SimulatorGraphSnapshot {
  return {
    equivalent: 'UAH',
    generated_at: '2026-10-16T00:00:00Z',
    nodes: [{ id: nodeId, name: nodeId, type: 'person', status: 'active' }],
    links: [],
  } as unknown as SimulatorGraphSnapshot
}

const RUN_STATUS = (runId: string) => ({
  run_id: runId,
  scenario_id: 'sc1',
  state: 'running',
  api_version: 'simulator-api/1',
  mode: 'real',
  sim_time_ms: 0,
  intensity_percent: 30,
  ops_sec: 0,
  queue_depth: 0,
  last_event_type: null,
  current_phase: null,
  last_error: null,
})

async function settle(): Promise<void> {
  for (let i = 0; i < 20; i += 1) {
    await Promise.resolve()
    await nextTick()
  }
}

let scope: EffectScope | null = null

function boot() {
  scope = effectScope()
  return scope.run(() => useSimulatorApp())!
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
  vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  window.history.replaceState({}, '', '/?mode=real')
  vi.mocked(listScenarios).mockResolvedValue({
    api_version: 'simulator-api/1',
    items: [
      { scenario_id: 'sc1', label: 'Scenario 1' },
      { scenario_id: 'sc2', label: 'Scenario 2' },
    ],
  } as never)
  // no active run: the scene is the scenario preview
  vi.mocked(getActiveRun).mockResolvedValue({} as never)
  vi.mocked(getRun).mockImplementation((async (_cfg: unknown, runId: string) => RUN_STATUS(runId)) as never)
  vi.mocked(getSnapshot).mockImplementation((async (_cfg: unknown, runId: string) => snap(`${runId}_NODE`)) as never)
  vi.mocked(getScenarioPreview).mockImplementation((async (_cfg: unknown, scenarioId: string) => snap(`PREVIEW_${scenarioId}`)) as never)
  vi.mocked(createRun).mockResolvedValue({ run_id: 'R2' } as never)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  scope?.stop()
  scope = null
  window.history.replaceState({}, '', '/')
  window.localStorage.clear()
  vi.clearAllMocks()
})

describe('042 S2: the client no longer chooses a run mode (reproducers: red while `desiredMode` fed the requests)', () => {
  it('a stored `fixtures` choice from the removed Pipeline menu is dropped on load and the run is created with `real`', async () => {
    window.localStorage.setItem(MODE_KEY, 'fixtures')
    window.localStorage.setItem('geo.sim.v2.intensityPercent', '55')

    const app = boot()
    await settle()

    expect(window.localStorage.getItem(MODE_KEY), 'the stale key is still stored').toBeNull()
    // only that key: another preference of the same person is untouched
    expect(window.localStorage.getItem('geo.sim.v2.intensityPercent')).toBe('55')

    await app.realActions.startRun()
    await settle()

    expect(createRun).toHaveBeenCalledTimes(1)
    expect(vi.mocked(createRun).mock.calls[0]?.[1]).toMatchObject({ scenario_id: 'sc1', mode: 'real' })
  })

  it('a stored `fixtures` choice reaches neither the scenario preview source nor its request', async () => {
    window.localStorage.setItem(MODE_KEY, 'fixtures')

    const app = boot()
    await settle()

    expect(getScenarioPreview).toHaveBeenCalled()
    // the preview function takes no mode at all: its third argument is the last one
    for (const call of vi.mocked(getScenarioPreview).mock.calls) expect(call.length).toBe(3)
    expect(app.state.sourcePath).toContain('/graph/preview?')
    expect(app.state.sourcePath).toContain('mode=real')
    expect(app.state.sourcePath).not.toContain('fixtures')
  })

  it('the state and the action of the removed choice do not exist, and nothing writes the key again', async () => {
    const app = boot()
    await settle()

    expect('desiredMode' in app.real).toBe(false)
    expect('setDesiredMode' in app.realActions).toBe(false)

    app.realActions.setSelectedScenarioId('sc2')
    app.realActions.setIntensityPercent(40)
    await settle()
    expect(window.localStorage.getItem(MODE_KEY)).toBeNull()
  })

  it('anti-vacuum: the same boot still starts a run, and the preview follows the scenario switch', async () => {
    const app = boot()
    await settle()
    expect(app.state.snapshot?.nodes.map((n) => n.id)).toEqual(['PREVIEW_sc1'])

    app.realActions.setSelectedScenarioId('sc2')
    // the scene reload after a scenario switch is debounced by the production code
    await vi.waitFor(() => expect(app.state.snapshot?.nodes.map((n) => n.id)).toEqual(['PREVIEW_sc2']))
    expect(vi.mocked(getScenarioPreview).mock.calls.at(-1)?.[1]).toBe('sc2')

    await app.realActions.startRun({ intensityPercent: 0, pauseImmediately: true })
    await settle()
    expect(app.real.runId).toBe('R2')
    expect(vi.mocked(createRun).mock.calls[0]?.[1]).toMatchObject({ scenario_id: 'sc2', mode: 'real', intensity_percent: 0 })
  })
})
