/**
 * 034 S8, F-034-D2 (reproducer of T3404): a failed scene load is not a successful empty network.
 *
 * `loadSnapshotForUi` used to turn a failed scenario preview into `nodes: [], links: []` with an empty error - an
 * empty network with no debts, which looks like data. The scene owner (`useSceneState`) already keeps the shown
 * scene and fills `state.error` when its loader throws - the loader simply never threw. Fixed for the PREVIEW.
 * The second half of the finding (a failed snapshot of a live run becomes the scenario preview) is NOT fixed: see
 * the note at the narrowed cases below.
 *
 * Every case goes through the real entrances of `useSimulatorApp` (boot, scenario choice, manual refresh), as in
 * `useSimulatorApp.unknownRunStatus.test.ts`. Cases marked CONTRACT pin what stays by design.
 *
 * Not judged here: how the error is worded or drawn (the existing error surface shows `state.error` as is).
 * 048 S3: WHICH failure is on screen is read from the value's technical detail (`runErrorLineLog`): the drawn text is
 * the sentence of the failure's kind since then (the transport's «HTTP 503 … /graph/preview» is the title, not the line).
 */
import { effectScope, nextTick, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/http'
import { createRun, getActiveRun, getRun, getScenarioPreview, getSnapshot, listScenarios } from '../api/simulatorApi'
import { connectSse } from '../api/sse'
import type { SimulatorGraphSnapshot } from '../api/simulatorTypes'
import { useSimulatorApp } from './useSimulatorApp'
import { t } from '../i18n/t'
import { runErrorLineLog } from '../utils/runErrorClassification'

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
    (opts: { url: string; signal?: AbortSignal; onMessage: (msg: { id?: string; data?: string }) => void }) =>
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
  for (const mock of [listScenarios, getActiveRun, getRun, getSnapshot, getScenarioPreview, createRun]) vi.mocked(mock).mockReset()
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
  it('on the first load: no empty network is shown as loaded, and the error is', async () => {
    withoutRun()
    vi.mocked(getScenarioPreview).mockRejectedValue(HTTP(503, '/simulator/scenarios/sc1/graph/preview'))

    const app = boot()
    await settle()

    expect(app.state.snapshot, 'a failed preview became a loaded empty network').toBeNull()
    expect(runErrorLineLog(app.state.errorLine)).toContain('HTTP 503')
  })

  it('on a refresh of the shown preview: the shown scene stays and the error is shown', async () => {
    withoutRun()
    const app = boot()
    await settle()
    expect(ids(app), 'precondition: the preview is shown').toEqual(['PREVIEW_NODE'])

    vi.mocked(getScenarioPreview).mockRejectedValue(new TypeError('Failed to fetch'))
    await app.realActions.refreshSnapshot()
    await settle()

    expect(ids(app), 'the shown preview was replaced by an empty network').toEqual(['PREVIEW_NODE'])
    expect(runErrorLineLog(app.state.errorLine)).toContain('Failed to fetch')
  })

  it('a preview that answers 404 (the scenario is gone) is an error on screen too, and it is not cleared', async () => {
    withoutRun()
    const app = boot()
    await settle()
    expect(ids(app)).toEqual(['PREVIEW_NODE'])

    vi.mocked(getScenarioPreview).mockRejectedValue(HTTP(404, '/simulator/scenarios/sc1/graph/preview'))
    await app.realActions.refreshSnapshot()
    await settle()

    expect(ids(app), 'the shown preview was replaced by an empty network').toEqual(['PREVIEW_NODE'])
    expect(runErrorLineLog(app.state.errorLine), 'the preview error was wiped as if it were a stale run').toContain('HTTP 404')
    // 048 S3: the scene failure's one place is the overlay (`state.error`); the run's line carries no copy of it.
    expect(runErrorLineLog(app.real.lastErrorLine) ?? '').not.toContain('/graph/preview')
  })
})

/**
 * NARROWED after the adversarial pass (finding F2). The first version of the D2 fix also threw a failed snapshot of
 * a LIVE run. Nothing re-reads that snapshot while the run lives (no periodic refresh; the SSE loop re-reads only
 * the status, and a paused run sends no events), so one transient 5xx at Start or at boot left the error overlay
 * and `data-ready="0"` for good over a working run. A retry needs a new mechanism with its own clock, which S8
 * does not build. So, for the snapshot of a live run, the behaviour from before S8 is back: a failure other than a
 * timeout or a 404 shows the scenario preview. KNOWN DEBT (F-034-D2, second half, open): that preview is another
 * graph in place of the run, with no mark on it.
 */
describe('F-034-D2, narrowed: a failed snapshot of a live run (behaviour from before S8, kept on purpose)', () => {
  it.each([
    ['a 503', () => HTTP(503, '/simulator/runs/R/graph/snapshot')],
    ['a network error', () => new TypeError('Failed to fetch')],
  ])('boot with a live run whose first snapshot read fails with %s: no error is stuck over the working run', async (_label, failure) => {
    vi.mocked(getSnapshot).mockRejectedValueOnce(failure())

    const app = boot()
    await settle()
    await vi.advanceTimersByTimeAsync(60_000)

    expect(app.real.runId, 'a failed read is not a stale run').toBe('R')
    expect(app.state.error, 'a transient failure left a permanent error over a live run').toBe('')
    expect(ids(app), 'nothing is on screen').toEqual(['PREVIEW_NODE'])
  })

  it('a refresh that fails with a 503 while the run scene is shown: the preview is shown, without an error', async () => {
    const app = boot()
    await settle()
    expect(ids(app), 'precondition: the run scene is shown').toEqual(['R_NODE'])

    vi.mocked(getSnapshot).mockRejectedValueOnce(HTTP(503, '/simulator/runs/R/graph/snapshot'))
    await app.realActions.refreshSnapshot()
    await settle()

    expect(app.state.error).toBe('')
    expect(ids(app)).toEqual(['PREVIEW_NODE'])
    expect(app.real.runId).toBe('R')

    // The next refresh that succeeds shows the run again.
    await app.realActions.refreshSnapshot()
    await settle()
    expect(ids(app)).toEqual(['R_NODE'])
  })

  it('when the preview fails as well, that IS an error on screen (the preview part of D2)', async () => {
    const app = boot()
    await settle()
    expect(ids(app)).toEqual(['R_NODE'])

    vi.mocked(getSnapshot).mockRejectedValue(HTTP(503, '/simulator/runs/R/graph/snapshot'))
    vi.mocked(getScenarioPreview).mockRejectedValue(HTTP(503, '/simulator/scenarios/sc1/graph/preview'))
    await app.realActions.refreshSnapshot()
    await settle()

    expect(ids(app), 'the shown scene was replaced by an empty network').toEqual(['R_NODE'])
    expect(runErrorLineLog(app.state.errorLine)).toContain('/graph/preview')
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

/**
 * External review of 034 S8, C1: the shown scene is kept under an error only when the load that failed asked for
 * the SAME source as the scene on screen (its `sourcePath`). A load for another run, scenario or equivalent that
 * fails leaves no scene: keeping the old one would let the events of the new context patch a graph they do not
 * belong to, and would show the old graph under the new selector.
 */
describe('F-034-D2, review C1: a failed load for ANOTHER source does not keep the old scene', () => {
  function bothFail(runId: string) {
    vi.mocked(getSnapshot).mockImplementation((async (_cfg: unknown, id: string, eq: string) => {
      if (id === runId) throw HTTP(503, `/simulator/runs/${id}/graph/snapshot`)
      return snap('SHARED', eq)
    }) as never)
    vi.mocked(getScenarioPreview).mockRejectedValue(HTTP(503, '/simulator/scenarios/sc1/graph/preview'))
  }

  it('Attach to run B whose snapshot and preview both fail: the scene of run A is gone, and an event of B changes nothing of A', async () => {
    vi.mocked(getSnapshot).mockImplementation((async (_cfg: unknown, _id: string, eq: string) => snap('SHARED', eq)) as never)
    const app = boot()
    await settle()
    expect(ids(app), 'precondition: the scene of run R is shown').toEqual(['SHARED'])
    const sceneOfA = app.state.snapshot!

    bothFail('B')
    await app.admin.attachRun('B')
    await settle()
    expect(app.real.runId, 'precondition: the page follows run B').toBe('B')

    const stream = vi.mocked(connectSse).mock.calls.at(-1)![0]
    expect(stream.url, 'precondition: the open stream is the stream of run B').toContain('/runs/B/events')
    stream.onMessage({
      id: 'evt_B_1',
      data: JSON.stringify({
        event_id: 'evt_B_1',
        ts: '2026-10-11T00:00:01Z',
        type: 'tx.updated',
        equivalent: 'UAH',
        from: 'SHARED',
        to: 'OTHER',
        amount: '5.00',
        ttl_ms: 1200,
        edges: [{ from: 'SHARED', to: 'OTHER' }],
        node_patch: [{ id: 'SHARED', net_balance: '-5.00' }],
      }),
    })
    await settle()

    expect(sceneOfA.nodes[0]?.net_balance, 'an event of run B patched the graph of run A').toBeUndefined()
    expect(app.state.snapshot, 'the scene of run A stayed on screen while the page follows run B').toBeNull()
    expect(runErrorLineLog(app.state.errorLine)).toContain('HTTP 503')
  })

  it('a switch of the equivalent whose load fails: the graph of the old equivalent is not left under the new selector', async () => {
    withoutRun()
    const app = boot()
    await settle()
    expect(app.state.snapshot?.equivalent).toBe('UAH')

    vi.mocked(getScenarioPreview).mockRejectedValue(HTTP(503, '/simulator/scenarios/sc1/graph/preview'))
    app.eq.value = 'EUR'
    await settle()

    expect(app.state.snapshot, 'the UAH graph is shown while EUR is selected').toBeNull()
    expect(runErrorLineLog(app.state.errorLine)).toContain('HTTP 503')
  })

  it('a choice of another scenario whose preview fails: the graph of the old scenario is not left on screen', async () => {
    withoutRun()
    const app = boot()
    await settle()
    expect(ids(app)).toEqual(['PREVIEW_NODE'])

    vi.mocked(getScenarioPreview).mockRejectedValue(HTTP(503, '/simulator/scenarios/sc2/graph/preview'))
    app.realActions.setSelectedScenarioId('sc2')
    await settle()

    expect(app.state.snapshot).toBeNull()
    expect(runErrorLineLog(app.state.errorLine)).toContain('HTTP 503')
  })
})

/**
 * External review of 034 S8, C2: the error the top HUD shows for a failed scene load (`real.lastError`, a copy of
 * `state.error`) goes away when that load recovers - and only that error: what the HUD showed before it (a run's
 * own `last_error`) comes back.
 *
 * 048 S3 (decision 3, «одна форма ошибки»), rewritten - not removed: the HUD copy is gone - a scene failure has ONE place,
 * the overlay over the canvas (`state.error`). What these tests held - "the run's own failure is on the line again after
 * the scene recovers" - now holds THROUGHOUT: the scene failure never takes the line. Red if the copy comes back.
 */
describe('F-034-D2, review C2 -> 048 S3: a scene error is not copied to the run line; the line keeps the run\'s own', () => {
  // 043 S2: "without a run" at opening now means the opening's own start was refused (decision 2: opening starts one);
  // that refusal is what the HUD shows before the scene error, and what it must show while and after the scene fails.
  it('without a run: the preview fails, then loads - the refused start stays on the line throughout; the scene error is the overlay\'s', async () => {
    withoutRun()
    vi.mocked(createRun).mockRejectedValue(HTTP(503, '/simulator/runs'))
    const app = boot()
    await settle()
    expect(app.real.runId, 'precondition: no run').toBeNull()
    // 048 S3: both failures are 503 - the same sentence; WHICH one is on the line is its detail (not vacuous by text).
    const before = runErrorLineLog(app.real.lastErrorLine)
    expect(before, 'precondition: the HUD shows the refused start').toContain('/simulator/runs')
    vi.mocked(getScenarioPreview).mockRejectedValueOnce(HTTP(503, '/simulator/scenarios/sc1/graph/preview'))
    await app.realActions.refreshSnapshot()
    await settle()
    expect(runErrorLineLog(app.state.errorLine), 'the scene error is shown - in its own place').toContain('/graph/preview')
    expect(runErrorLineLog(app.real.lastErrorLine), 'not copied to the run line: the refused start stays').toBe(before)

    await app.realActions.refreshSnapshot()
    await settle()

    expect(app.state.error).toBe('')
    expect(runErrorLineLog(app.real.lastErrorLine), 'the scene recovered, the line still shows the refused start').toBe(before)
    expect(app.real.lastError).toBe(t('failure.serverFailed'))
  })

  // 043 S2: a run found ENDED at opening is replaced by a live one (decision 2), so the run here stops with an error
  // AFTER the opening - the opening attaches to it while it runs, and a later read of its status (Admin -> Attach to the
  // same run) finds it stopped with an error.
  it('a run stopped with an error: its own error stays on the line while the scene fails and after', async () => {
    let stopped = false
    vi.mocked(getRun).mockImplementation((async (_cfg: unknown, runId: string) => (stopped ? {
      ...RUN_STATUS(runId),
      state: 'error',
      last_error: { code: 'REAL_MODE_TICK_FAILED', message: 'tick failed', at: '2026-10-11T00:00:00Z' },
    } : RUN_STATUS(runId))) as never)
    const app = boot()
    await settle()
    expect(createRun, 'precondition: the opening attached to the live run and started none').not.toHaveBeenCalled()
    stopped = true
    await app.admin.attachRun('R')
    await settle()
    // 046 wave 1, full (2026-10-19): the line of a run ended by an error is the interface's sentence with the code.
    expect(app.real.lastError, 'precondition: the HUD shows why the run stopped').toBe(t('runLine.errorStopped', { code: 'REAL_MODE_TICK_FAILED' }, 'en'))

    vi.mocked(getScenarioPreview).mockRejectedValueOnce(HTTP(503, '/simulator/scenarios/sc1/graph/preview'))
    await app.realActions.refreshSnapshot()
    await settle()
    expect(runErrorLineLog(app.state.errorLine), 'the scene error is shown - in its own place').toContain('HTTP 503')
    expect(app.real.lastError, 'not copied to the run line').toBe(t('runLine.errorStopped', { code: 'REAL_MODE_TICK_FAILED' }, 'en'))

    await app.realActions.refreshSnapshot()
    await settle()

    expect(app.real.lastError, 'the error of the run was lost with the scene error').toBe(t('runLine.errorStopped', { code: 'REAL_MODE_TICK_FAILED' }, 'en'))
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
