/**
 * 044 stage 2 - the visitor's side of "a run of one's own" on the real composable (API mocked at the module boundary):
 * the visitor's tempo (15) against the operator's (30); a tempo below 1 is refused on Start and Apply (BACKLOG 043-19).
 */
import { effectScope, nextTick, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/http'
import { connectSse } from '../api/sse'
import {
  createRun, getActiveRun, getRun, getScenarioPreview, getSnapshot, listScenarios, pauseRun, resumeRun, setIntensity, stopRun,
} from '../api/simulatorApi'
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
    pauseRun: vi.fn(),
    resumeRun: vi.fn(),
    stopRun: vi.fn(),
    setIntensity: vi.fn(),
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

function snap(nodeId: string): SimulatorGraphSnapshot {
  return {
    equivalent: 'UAH',
    generated_at: '2026-10-16T00:00:00Z',
    nodes: [{ id: nodeId, name: nodeId, type: 'person', status: 'active' }, { id: 'B', name: 'B', type: 'person', status: 'active' }],
    // One line between the two nodes (the node of the run is named after it): "Inspect edge" and a line click need it.
    links: [{ source: nodeId, target: 'B', trust_limit: '100', used: '0', available: '100', status: 'active' }],
  } as unknown as SimulatorGraphSnapshot
}

/** The backend's runs, by id: their state follows create / pause / resume / stop, as the server's does. */
const runs = new Map<string, string>()
const status = (runId: string) => ({
  run_id: runId, scenario_id: 'sc1', state: runs.get(runId) ?? 'running', api_version: 'simulator-api/1', mode: 'real',
  sim_time_ms: 0, intensity_percent: 30, ops_sec: 0, queue_depth: 0, last_event_type: null, current_phase: null, last_error: null,
})

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
/** A returning visitor: the run id the browser kept, whose state the backend reports. */
function storedRun(runId: string, state: string) {
  window.localStorage.setItem('geo.sim.v2.runId', runId)
  runs.set(runId, state)
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
  vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  runs.clear()
  for (const m of [listScenarios, getActiveRun, getRun, getSnapshot, getScenarioPreview, createRun, pauseRun, resumeRun, stopRun]) vi.mocked(m).mockReset()
  vi.mocked(connectSse).mockClear()
  vi.mocked(listScenarios).mockResolvedValue({ api_version: 'simulator-api/1', items: [{ scenario_id: 'sc1', label: 'Scenario 1' }] } as never)
  vi.mocked(getActiveRun).mockResolvedValue({ run_id: null } as never)
  vi.mocked(getRun).mockImplementation((async (_c: unknown, id: string) => status(id)) as never)
  vi.mocked(getSnapshot).mockImplementation((async (_c: unknown, id: string) => snap(`${id}_NODE`)) as never)
  vi.mocked(getScenarioPreview).mockImplementation((async () => snap('PREVIEW_NODE')) as never)
  vi.mocked(createRun).mockImplementation((async () => { runs.set('R_NEW', 'running'); return { run_id: 'R_NEW' } }) as never)
  vi.mocked(pauseRun).mockImplementation((async (_c: unknown, id: string) => { runs.set(id, 'paused'); return status(id) }) as never)
  vi.mocked(resumeRun).mockImplementation((async (_c: unknown, id: string) => { runs.set(id, 'running'); return status(id) }) as never)
  vi.mocked(stopRun).mockImplementation((async (_c: unknown, id: string) => { runs.set(id, 'stopped'); return status(id) }) as never)
  window.history.replaceState({}, '', '/')
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  scope?.stop()
  scope = null
  window.history.replaceState({}, '', '/')
  window.localStorage.clear()
})

const asVisitor = () => window.localStorage.setItem('geo.sim.v2.accessToken', '')

describe('044 stage 2: the visitor tempo', () => {
  it('a visitor (no admin token): the opening run starts at the visitor tempo 15, and the field shows 15', async () => {
    asVisitor()
    const app = boot()
    await settle()
    expect(createRun).toHaveBeenCalledTimes(1)
    expect(vi.mocked(createRun).mock.calls[0]![1]).toMatchObject({ intensity_percent: 15 })
    expect(app.real.intensityPercent, 'the tempo field says what the run runs at').toBe(15)
  })

  it("the operator (an admin token): the opening run and Start keep 30", async () => {
    window.localStorage.setItem('geo.sim.v2.accessToken', 'an-admin-token')
    const app = boot()
    await settle()
    expect(vi.mocked(createRun).mock.calls[0]![1]).toMatchObject({ intensity_percent: 30 })
    expect(app.real.intensityPercent).toBe(30)
  })

  it('a participant JWT is not the operator: 15', async () => {
    window.localStorage.setItem('geo.sim.v2.accessToken', 'a.b.c')
    boot()
    await settle()
    expect(vi.mocked(createRun).mock.calls[0]![1]).toMatchObject({ intensity_percent: 15 })
  })

  it("a stored preference still wins for the visitor (55)", async () => {
    asVisitor()
    window.localStorage.setItem('geo.sim.v2.intensityPercent', '55')
    boot()
    await settle()
    expect(vi.mocked(createRun).mock.calls[0]![1]).toMatchObject({ intensity_percent: 55 })
  })
})

describe('044 stage 2 (BACKLOG 043-19): a tempo of 0 typed by hand starts no silent run', () => {
  it('Start with 0: no create, a readable hint on the error line', async () => {
    storedRun('R_OLD', 'stopped')
    vi.mocked(createRun).mockRejectedValueOnce(new ApiError('HTTP 503 for /simulator/runs', { status: 503 }))
    document.documentElement.lang = 'ru'
    try {
      const app = boot()
      await settle()
      vi.mocked(createRun).mockClear()
      app.realActions.setIntensityPercent(0)
      await app.realActions.startRun()
      await settle()
      expect(createRun, 'no run at 0').not.toHaveBeenCalled()
      expect(app.real.lastError).toBe('Темп 0 — прогон без платежей: укажите темп от 1 до 100. Остановить платежи — пауза.')
      // counter-check: the same Start with 1 creates
      app.realActions.setIntensityPercent(1)
      await app.realActions.startRun()
      await settle()
      expect(vi.mocked(createRun).mock.calls[0]![1]).toMatchObject({ intensity_percent: 1 })
    } finally {
      document.documentElement.lang = ''
    }
  })

  it('Apply with 0 on a live run: the intensity is not sent, the hint is shown', async () => {
    const app = boot()
    await settle()
    expect(app.real.runStatus?.state).toBe('running')
    app.realActions.setIntensityPercent(0)
    await app.realActions.applyIntensity()
    expect(setIntensity, 'no intensity request').not.toHaveBeenCalled()
    expect(app.real.lastError).toContain('A tempo of 0 makes no payments')
  })
})
