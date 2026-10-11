/**
 * 034 S8, F-034-D2 (reproducer of T3404): a failed scene load is not a successful empty network.
 *
 * `loadSnapshotForUi` used to turn a failed scenario preview into `nodes: [], links: []` with an empty error, and a
 * failed snapshot of a live run into the scenario preview. Both look like data: an empty network with no debts, or
 * another graph shown as the run. The scene owner (`useSceneState`) already keeps the shown scene and fills
 * `state.error` when its loader throws - the loader simply never threw.
 *
 * Every case goes through the real entrances of `useSimulatorApp` (boot, scenario choice, manual refresh), as in
 * `useSimulatorApp.unknownRunStatus.test.ts`. Cases marked CONTRACT pin what stays by design.
 *
 * Not judged here: how the error is worded or drawn (the existing error surface shows `state.error` as is).
 */
import { effectScope, nextTick, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/http'
import { getActiveRun, getRun, getScenarioPreview, getSnapshot, listScenarios } from '../api/simulatorApi'
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

function snap(nodeId: string, equivalent = 'UAH'): SimulatorGraphSnapshot {
  return {
    equivalent,
    generated_at: '2026-10-11T00:00:00Z',
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

const HTTP = (status: number, path: string) =>
  new ApiError(`HTTP ${status} ${status === 404 ? 'Not Found' : 'Service Unavailable'} for ${path}`, { status })

/** Lets the pending work finish, the 80 ms coalescing window of `refreshSnapshot` included (fake clock). */
async function settle(): Promise<void> {
  for (let i = 0; i < 20; i += 1) {
    await Promise.resolve()
    await nextTick()
  }
  await vi.advanceTimersByTimeAsync(200)
  for (let i = 0; i < 20; i += 1) {
    await Promise.resolve()
    await nextTick()
  }
}

const ids = (app: ReturnType<typeof useSimulatorApp>) => app.state.snapshot?.nodes.map((n) => n.id)

let scope: EffectScope | null = null

function boot() {
  scope = effectScope()
  return scope.run(() => useSimulatorApp())!
}

/** No run: the screen shows the preview of the selected scenario. */
function withoutRun() {
  vi.mocked(getActiveRun).mockResolvedValue({ run_id: null } as never)
}

beforeEach(() => {
  vi.useFakeTimers()
  for (const mock of [listScenarios, getActiveRun, getRun, getSnapshot, getScenarioPreview]) vi.mocked(mock).mockReset()
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
  vi.mocked(getActiveRun).mockResolvedValue({ run_id: 'R' } as never)
  vi.mocked(getRun).mockImplementation((async (_cfg: unknown, runId: string) => RUN_STATUS(runId)) as never)
  vi.mocked(getSnapshot).mockImplementation((async (_cfg: unknown, runId: string, eq: string) =>
    snap(`${runId}_NODE`, eq)) as never)
  vi.mocked(getScenarioPreview).mockImplementation((async (_cfg: unknown, scenarioId: string) =>
    snap(scenarioId === 'sc1' ? 'PREVIEW_NODE' : `PREVIEW_${scenarioId}`)) as never)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  scope?.stop()
  scope = null
  window.history.replaceState({}, '', '/')
  window.localStorage.clear()
  vi.clearAllMocks()
})

describe('F-034-D2: a failed scenario preview', () => {
  // T3404: red on origin/main because of F-034-D2; S8 removes `.fails` together with the fix.
  it.fails('on the first load: no empty network is shown as loaded, and the error is', async () => {
    withoutRun()
    vi.mocked(getScenarioPreview).mockRejectedValue(HTTP(503, '/simulator/scenarios/sc1/graph/preview'))

    const app = boot()
    await settle()

    expect(app.state.snapshot, 'a failed preview became a loaded empty network').toBeNull()
    expect(app.state.error).toContain('HTTP 503')
  })

  // T3404: red on origin/main because of F-034-D2; S8 removes `.fails` together with the fix.
  it.fails('on a refresh of the shown preview: the shown scene stays and the error is shown', async () => {
    withoutRun()
    const app = boot()
    await settle()
    expect(ids(app), 'precondition: the preview is shown').toEqual(['PREVIEW_NODE'])

    vi.mocked(getScenarioPreview).mockRejectedValue(new TypeError('Failed to fetch'))
    await app.realActions.refreshSnapshot()
    await settle()

    expect(ids(app), 'the shown preview was replaced by an empty network').toEqual(['PREVIEW_NODE'])
    expect(app.state.error).toContain('Failed to fetch')
  })

  // T3404: red on origin/main because of F-034-D2; S8 removes `.fails` together with the fix.
  it.fails('a preview that answers 404 (the scenario is gone) is an error on screen too, and it is not cleared', async () => {
    withoutRun()
    const app = boot()
    await settle()
    expect(ids(app)).toEqual(['PREVIEW_NODE'])

    vi.mocked(getScenarioPreview).mockRejectedValue(HTTP(404, '/simulator/scenarios/sc1/graph/preview'))
    await app.realActions.refreshSnapshot()
    await settle()

    expect(ids(app), 'the shown preview was replaced by an empty network').toEqual(['PREVIEW_NODE'])
    expect(app.state.error, 'the preview error was wiped as if it were a stale run').toContain('HTTP 404')
    expect(app.real.lastError).toContain('HTTP 404')
  })
})

describe('F-034-D2: a failed snapshot of a live run', () => {
  // T3404: red on origin/main because of F-034-D2; S8 removes `.fails` together with the fix.
  it.fails.each([
    ['a 503', () => HTTP(503, '/simulator/runs/R/graph/snapshot')],
    ['a network error', () => new TypeError('Failed to fetch')],
  ])('%s: the run scene is not replaced by the scenario preview, and the error is shown', async (_label, failure) => {
    const app = boot()
    await settle()
    expect(ids(app), 'precondition: the run scene is shown').toEqual(['R_NODE'])

    vi.mocked(getSnapshot).mockRejectedValue(failure())
    await app.realActions.refreshSnapshot()
    await settle()

    expect(ids(app), 'the run scene was replaced by the scenario preview').toEqual(['R_NODE'])
    expect(app.state.error).not.toBe('')
    expect(app.real.runId, 'a failed read is not a stale run').toBe('R')
  })

  it('CONTRACT: a 404 means the run is gone - it is reset and the preview of the scenario is the scene', async () => {
    const app = boot()
    await settle()
    expect(ids(app)).toEqual(['R_NODE'])

    vi.mocked(getSnapshot).mockRejectedValue(HTTP(404, '/simulator/runs/R/graph/snapshot'))
    vi.mocked(getRun).mockRejectedValue(HTTP(404, '/simulator/runs/R'))
    vi.mocked(getActiveRun).mockResolvedValue({ run_id: null } as never)
    await app.realActions.refreshSnapshot()
    await settle()

    expect(app.real.runId).toBeNull()
    expect(ids(app)).toEqual(['PREVIEW_NODE'])
    expect(app.state.error).toBe('')
  })
})

describe('F-034-D2: what stays', () => {
  it('CONTRACT: the next successful load clears the error and shows the new data', async () => {
    withoutRun()
    const app = boot()
    await settle()
    vi.mocked(getScenarioPreview).mockRejectedValueOnce(HTTP(503, '/simulator/scenarios/sc1/graph/preview'))
    await app.realActions.refreshSnapshot()
    await settle()

    await app.realActions.refreshSnapshot()
    await settle()

    expect(ids(app)).toEqual(['PREVIEW_NODE'])
    expect(app.state.error).toBe('')
  })

  it('anti-vacuum: a preview that loads is shown', async () => {
    withoutRun()
    const app = boot()
    await settle()

    expect(ids(app)).toEqual(['PREVIEW_NODE'])
    expect(app.state.error).toBe('')
  })
})
