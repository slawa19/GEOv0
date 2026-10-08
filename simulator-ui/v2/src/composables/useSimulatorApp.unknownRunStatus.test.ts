/**
 * 034 S5b, the remainder of S5a: an UNKNOWN run status (`runStatus === null` while `runId` is set - for instance
 * after `GET /runs/{id}` timed out) must not demote the run scene that is already on screen to the scenario preview.
 *
 * Every case goes through the REAL entrances of `useSimulatorApp` (`admin.attachRun`, `realActions.startRun`, the
 * boot with a saved run id, the equivalent switch), because they all meet in one place - the choice between the
 * run snapshot and the preview in `loadSnapshotForUi` - and a test of that place alone does not show that the
 * entrances reach it.
 *
 * Cases marked CONTRACT pin what stays by design: when no scene of that run is shown there is nothing to demote,
 * and the preview is the right thing to show (the stale-run-id contract of the scenario watcher).
 *
 * The SSE mock REJECTS on abort, as the production `connectSse` does (`api/sse.ts:75,88`).
 */
import { effectScope, nextTick, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { API_TIMEOUT_CODE, ApiError } from '../api/http'
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

function snap(nodeId: string, equivalent = 'UAH'): SimulatorGraphSnapshot {
  return {
    equivalent,
    generated_at: '2026-10-08T00:00:00Z',
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

const STATUS_TIMEOUT = () =>
  new ApiError('GET /simulator/runs/R -> timeout after 30000ms', { status: 0, code: API_TIMEOUT_CODE, timeoutMs: 30_000 })

async function settle(): Promise<void> {
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
  vi.mocked(getActiveRun).mockResolvedValue({ run_id: 'R' } as never)
  vi.mocked(getRun).mockImplementation((async (_cfg: unknown, runId: string) => RUN_STATUS(runId)) as never)
  vi.mocked(getSnapshot).mockImplementation((async (_cfg: unknown, runId: string, eq: string) =>
    snap(eq === 'UAH' ? `${runId}_NODE` : `${runId}_NODE_${eq}`, eq)) as never)
  vi.mocked(getScenarioPreview).mockImplementation((async (_cfg: unknown, scenarioId: string) =>
    snap(scenarioId === 'sc1' ? 'PREVIEW_NODE' : `PREVIEW_${scenarioId}`)) as never)
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

describe('unknown run status while the run scene is on screen (reproducers: red before the rule)', () => {
  it('Attach to the SAME run, status read times out: the run scene stays a run scene', async () => {
    const app = boot()
    await settle()
    expect(ids(app), 'precondition: the run scene is shown').toEqual(['R_NODE'])

    vi.mocked(getRun).mockRejectedValue(STATUS_TIMEOUT())
    await app.admin.attachRun('R')
    await settle()

    expect(ids(app), 'the run scene was demoted to the scenario preview').toEqual(['R_NODE'])
    expect(app.real.lastError).toContain('timeout')
  })

  it('Attach to the same run, status AND snapshot time out: the scene stays and the error is shown', async () => {
    const app = boot()
    await settle()
    expect(ids(app)).toEqual(['R_NODE'])

    vi.mocked(getRun).mockRejectedValue(STATUS_TIMEOUT())
    vi.mocked(getSnapshot).mockRejectedValue(STATUS_TIMEOUT())
    await app.admin.attachRun('R')
    await settle()

    expect(ids(app), 'the run scene was demoted to the scenario preview').toEqual(['R_NODE'])
    expect(app.state.error).toContain('timeout')
  })

  it('Manual refresh while the status is unknown (after such an Attach) keeps the run scene', async () => {
    const app = boot()
    await settle()
    vi.mocked(getRun).mockRejectedValue(STATUS_TIMEOUT())
    await app.admin.attachRun('R')
    await settle()
    expect(app.real.runStatus, 'precondition: the status is unknown').toBeNull()

    await app.realActions.refreshSnapshot()
    await settle()

    expect(ids(app), 'the run scene was demoted to the scenario preview').toEqual(['R_NODE'])
  })

  it('Equivalent switch while the status is unknown: the run snapshot of the NEW equivalent is read', async () => {
    const app = boot()
    await settle()
    vi.mocked(getRun).mockRejectedValue(STATUS_TIMEOUT())
    await app.admin.attachRun('R')
    await settle()
    expect(app.real.runStatus, 'precondition: the status is unknown').toBeNull()
    expect(ids(app), 'precondition: the run scene is shown').toEqual(['R_NODE'])

    app.eq.value = 'EUR'
    await settle()

    expect(ids(app), 'the run scene was replaced by the scenario preview').toEqual(['R_NODE_EUR'])
  })
})

describe('what the unknown-status rule must NOT override (review of 7360403e, both red before)', () => {
  it('an explicit choice of ANOTHER scenario still leads to its preview', async () => {
    const app = boot()
    await settle()
    vi.mocked(getRun).mockRejectedValue(STATUS_TIMEOUT())
    await app.admin.attachRun('R')
    await settle()
    expect(app.real.runStatus, 'precondition: the status is unknown').toBeNull()
    expect(ids(app), 'precondition: the run scene stays').toEqual(['R_NODE'])

    app.realActions.setSelectedScenarioId('sc2')
    await settle()

    expect(ids(app), 'the unknown status of the shown run overrode the explicit scenario choice').toEqual(['PREVIEW_sc2'])
  })

  it('a LATE snapshot of another run, which the scene owner rejects, does not take over the shown-run record', async () => {
    const app = boot()
    await settle()
    expect(ids(app)).toEqual(['R_NODE'])

    // 1. an equivalent switch starts a load of run R that is slow
    let releaseLateR: (s: SimulatorGraphSnapshot) => void = () => undefined
    const lateR = new Promise<SimulatorGraphSnapshot>((resolve) => {
      releaseLateR = resolve
    })
    vi.mocked(getSnapshot).mockImplementation(((_cfg: unknown, runId: string, eq: string) =>
      runId === 'R' && eq === 'EUR' ? lateR : Promise.resolve(snap(`${runId}_NODE_${eq}`, eq))) as never)
    app.eq.value = 'EUR'
    await settle()

    // 2. meanwhile the operator attaches run B, whose scene is accepted
    await app.admin.attachRun('B')
    await settle()
    expect(ids(app), 'precondition: the scene of B is on screen').toEqual(['B_NODE_EUR'])

    // 3. the slow load of R answers; the scene owner rejects it (a newer load won)
    releaseLateR(snap('R_NODE_EUR', 'EUR'))
    await settle()
    expect(ids(app), 'precondition: the late snapshot of R was rejected').toEqual(['B_NODE_EUR'])

    // 4. a re-attach of B whose status read fails: the scene on screen is B's, and stays B's
    vi.mocked(getRun).mockRejectedValue(STATUS_TIMEOUT())
    await app.admin.attachRun('B')
    await settle()

    expect(ids(app), 'the late load of R made the scene of B look like "no run scene" and it fell to the preview').toEqual([
      'B_NODE_EUR',
    ])
  })
})

describe('unknown run status with NO scene of that run on screen (CONTRACT: the preview stays, and the error must be visible)', () => {
  it('Boot with a saved run id whose status times out: nothing is on screen, the preview is shown and the error too', async () => {
    window.localStorage.setItem('geo.sim.v2.runId', 'R')
    vi.mocked(getRun).mockRejectedValue(STATUS_TIMEOUT())
    const app = boot()
    await settle()

    expect(ids(app)).toEqual(['PREVIEW_NODE'])
    expect(app.real.lastError).toContain('timeout')
  })

  it('Attach to ANOTHER run whose status times out: the scene of run R is not the scene of run R2', async () => {
    const app = boot()
    await settle()
    expect(ids(app)).toEqual(['R_NODE'])

    vi.mocked(getRun).mockRejectedValue(STATUS_TIMEOUT())
    await app.admin.attachRun('R2')
    await settle()

    expect(ids(app)).toEqual(['PREVIEW_NODE'])
    expect(app.real.lastError).toContain('timeout')
  })

  it('Start of a new run whose status times out: the old run scene is not the new run scene', async () => {
    const app = boot()
    await settle()
    expect(ids(app)).toEqual(['R_NODE'])

    vi.mocked(getRun).mockRejectedValue(STATUS_TIMEOUT())
    await app.realActions.startRun()
    await settle()

    expect(app.real.runId).toBe('R2')
    expect(ids(app)).toEqual(['PREVIEW_NODE'])
    expect(app.real.lastError).toContain('timeout')
  })

  it('anti-vacuum: with a KNOWN status everything behaves normally (Attach RE-READS the run scene)', async () => {
    const app = boot()
    await settle()
    expect(ids(app)).toEqual(['R_NODE'])
    // the run moved on: the next read answers with a different scene, so an Attach that did nothing is visible
    vi.mocked(getSnapshot).mockImplementation((async (_cfg: unknown, runId: string, eq: string) => snap(`${runId}_NODE_v2`, eq)) as never)

    await app.admin.attachRun('R')
    await settle()

    expect(ids(app), 'the Attach did not re-read the run snapshot').toEqual(['R_NODE_v2'])
    expect(app.real.runStatus?.run_id).toBe('R')
  })

  it('anti-vacuum: once the preview has replaced the run scene, an unknown status does not bring the run scene back', async () => {
    const app = boot()
    await settle()
    vi.mocked(getRun).mockImplementation((async (_cfg: unknown, runId: string) => ({ ...RUN_STATUS(runId), state: 'stopped' })) as never)
    await app.admin.attachRun('R')
    await settle()
    expect(ids(app), 'precondition: the preview replaced the scene of the stopped run').toEqual(['PREVIEW_NODE'])

    app.real.runStatus = null
    await app.realActions.refreshSnapshot()
    await settle()

    expect(ids(app)).toEqual(['PREVIEW_NODE'])
  })

  it('anti-vacuum: a known TERMINAL status still selects the preview (the run is over)', async () => {
    const app = boot()
    await settle()
    vi.mocked(getRun).mockImplementation((async (_cfg: unknown, runId: string) => ({ ...RUN_STATUS(runId), state: 'stopped' })) as never)
    await app.admin.attachRun('R')
    await settle()

    expect(ids(app)).toEqual(['PREVIEW_NODE'])
  })
})
