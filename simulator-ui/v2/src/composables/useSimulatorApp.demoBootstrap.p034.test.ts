/**
 * 034 S8, F-034-D7: a visitor with no access token (an anonymous cookie session) is not left without a run.
 *
 * Until 043 S3 this file held the eager paused bootstrap of the Demo UI page (`?mode=real&ui=demo`) - three tests,
 * removed with that page (043 decision 5). What stays is the same question asked of the one screen: the token-less
 * state of a production build (`DEV` stubbed off, because a dev build fills in the dev token) still gets its live run.
 */
import { effectScope, nextTick, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createRun, getActiveRun, getRun, getScenarioPreview, getSnapshot, listScenarios, pauseRun } from '../api/simulatorApi'
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
    generated_at: '2026-10-11T00:00:00Z',
    nodes: [{ id: nodeId, name: nodeId, type: 'person', status: 'active' }],
    links: [],
  } as unknown as SimulatorGraphSnapshot
}

const RUN_STATUS = (runId: string, state: string) => ({
  run_id: runId,
  scenario_id: 'sc1',
  state,
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

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
  vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  for (const mock of [listScenarios, getActiveRun, getRun, getSnapshot, getScenarioPreview, createRun, pauseRun]) {
    vi.mocked(mock).mockReset()
  }
  vi.mocked(listScenarios).mockResolvedValue({
    api_version: 'simulator-api/1',
    items: [{ scenario_id: 'sc1', label: 'Scenario 1' }],
  } as never)
  vi.mocked(getActiveRun).mockResolvedValue({ run_id: null } as never)
  vi.mocked(getRun).mockImplementation((async (_cfg: unknown, runId: string) => RUN_STATUS(runId, 'paused')) as never)
  vi.mocked(getSnapshot).mockImplementation((async (_cfg: unknown, runId: string) => snap(`${runId}_NODE`)) as never)
  vi.mocked(getScenarioPreview).mockImplementation((async () => snap('PREVIEW_NODE')) as never)
  vi.mocked(createRun).mockResolvedValue({ run_id: 'R_DEMO' } as never)
  vi.mocked(pauseRun).mockImplementation((async (_cfg: unknown, runId: string) => RUN_STATUS(runId, 'paused')) as never)
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

describe('F-034-D7: a token-less visitor and the opening run', () => {
  // 043 S2 (decision 2) REVERSES the contract this test held until 2026-10-16 ("outside the demo page nothing is started
  // for the visitor"): opening the demo is a live run. The one screen starts ONE run, not paused, at the default
  // intensity, for a visitor with no token too. The walk of the opening is `useSimulatorApp.autostart.p043.test.ts`.
  it('CONTRACT (043 S2): the one screen starts one live run for a token-less visitor, not paused', async () => {
    vi.stubEnv('DEV', false)
    window.history.replaceState({}, '', '/?mode=real')

    const app = boot()
    await settle()

    expect(app.real.accessToken, 'precondition: the visitor has no token').toBe('')
    expect(createRun).toHaveBeenCalledTimes(1)
    // 044 stage 2: a token-less visitor's run starts at the visitor tempo 15 (the operator's default stays 30).
    expect(vi.mocked(createRun).mock.calls[0]![1]).toMatchObject({ intensity_percent: 15 })
    expect(pauseRun, 'not paused').not.toHaveBeenCalled()
  })
})
