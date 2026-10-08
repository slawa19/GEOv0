/**
 * 034 S5a, fix-delta (external review of 03436d46), item 1: a run STATUS read that times out during an admin
 * Attach must not let the scenario preview replace the run scene that is already shown.
 *
 * This goes through the REAL entrance - `useSimulatorApp` -> `admin.attachRun` -> `attachToRun` ->
 * `refreshSnapshot` -> `loadSnapshotForUi` -, not through an extracted helper: the first fix of the snapshot
 * timeout was correct for the helper and still bypassed here, because attach sets `runStatus = null` and
 * `loadSnapshotForUi` then does not consider the run active at all.
 */
import { effectScope, nextTick, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { API_TIMEOUT_CODE, ApiError } from '../api/http'
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
    ensureSession: vi.fn(async () => ({ actor_kind: 'anonymous', owner_id: 'o1' })),
  }
})
vi.mock('../api/equivalentsApi', () => ({ fetchEquivalentPrecisions: vi.fn(async () => []) }))
vi.mock('../api/sse', () => ({
  connectSse: vi.fn(
    (opts: { signal?: AbortSignal }) =>
      new Promise<void>((resolve) => {
        if (opts.signal?.aborted) return resolve()
        opts.signal?.addEventListener('abort', () => resolve(), { once: true })
      }),
  ),
}))

function snap(nodeId: string): SimulatorGraphSnapshot {
  return {
    equivalent: 'UAH',
    generated_at: '2026-10-08T00:00:00Z',
    nodes: [{ id: nodeId, name: nodeId, type: 'person', status: 'active' }],
    links: [],
  } as unknown as SimulatorGraphSnapshot
}

const RUN_STATUS = {
  run_id: 'R',
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
}

async function settle(): Promise<void> {
  for (let i = 0; i < 20; i += 1) {
    await Promise.resolve()
    await nextTick()
  }
}

let scope: EffectScope | null = null

beforeEach(() => {
  // Nothing in this harness may reach a real network: anything not mocked above fails at once like an offline
  // fetch (NOT with a 404: a 404 on a run-scoped call resets the run, which is a different behavior).
  vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
  window.history.replaceState({}, '', '/?mode=real')
  vi.mocked(listScenarios).mockResolvedValue({
    api_version: 'simulator-api/1',
    items: [{ scenario_id: 'sc1', label: 'Scenario 1' }],
  } as never)
  vi.mocked(getActiveRun).mockResolvedValue({ run_id: 'R' } as never)
  vi.mocked(getRun).mockResolvedValue(RUN_STATUS as never)
  vi.mocked(getSnapshot).mockResolvedValue(snap('RUN_NODE'))
  vi.mocked(getScenarioPreview).mockResolvedValue(snap('PREVIEW_NODE'))
})

afterEach(() => {
  vi.unstubAllGlobals()
  scope?.stop()
  scope = null
  window.history.replaceState({}, '', '/')
  vi.clearAllMocks()
})

describe('admin Attach with a run status read that times out', () => {
  it('keeps the run scene that is shown and reports the timeout', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    scope = effectScope()
    const app = scope.run(() => useSimulatorApp())!
    await settle()
    expect(app.state.snapshot?.nodes.map((n) => n.id), 'precondition: the run scene is shown').toEqual(['RUN_NODE'])

    vi.mocked(getRun).mockRejectedValue(
      new ApiError('GET /simulator/runs/R -> timeout after 30000ms', { status: 0, code: API_TIMEOUT_CODE, timeoutMs: 30_000 }),
    )
    await app.admin.attachRun('R')
    await settle()

    expect(app.state.snapshot?.nodes.map((n) => n.id), 'the run scene was replaced by the scenario preview').toEqual([
      'RUN_NODE',
    ])
    expect(app.real.lastError).toContain('timeout')
  })

  it('counter-check: a 5xx on the status read keeps the existing behavior (the preview is shown)', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    scope = effectScope()
    const app = scope.run(() => useSimulatorApp())!
    await settle()
    expect(app.state.snapshot?.nodes.map((n) => n.id)).toEqual(['RUN_NODE'])

    vi.mocked(getRun).mockRejectedValue(new ApiError('HTTP 503  for /simulator/runs/R', { status: 503 }))
    await app.admin.attachRun('R')
    await settle()

    expect(app.state.snapshot?.nodes.map((n) => n.id)).toEqual(['PREVIEW_NODE'])
  })

  it('anti-vacuum: a status read that answers attaches normally and shows the run scene', async () => {
    scope = effectScope()
    const app = scope.run(() => useSimulatorApp())!
    await settle()

    await app.admin.attachRun('R')
    await settle()

    expect(app.state.snapshot?.nodes.map((n) => n.id)).toEqual(['RUN_NODE'])
    expect(app.real.runStatus?.run_id).toBe('R')
  })
})

describe('a run SNAPSHOT read that fails while the run is active (through the real scene loader)', () => {
  const TIMEOUT = () =>
    new ApiError('GET /simulator/runs/R/graph/snapshot -> timeout after 30000ms', { status: 0, code: API_TIMEOUT_CODE, timeoutMs: 30_000 })

  it('a timeout keeps the run scene and reports the error in state.error', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    scope = effectScope()
    const app = scope.run(() => useSimulatorApp())!
    await settle()
    expect(app.state.snapshot?.nodes.map((n) => n.id)).toEqual(['RUN_NODE'])

    vi.mocked(getSnapshot).mockRejectedValue(TIMEOUT())
    await app.admin.attachRun('R')
    await settle()

    expect(app.state.snapshot?.nodes.map((n) => n.id), 'the run scene was replaced by the scenario preview').toEqual(['RUN_NODE'])
    expect(app.state.error).toContain('timeout')
  })

  it.each([
    ['a 503', () => new ApiError('HTTP 503  for /x', { status: 503 })],
    ['a network error', () => new TypeError('Failed to fetch')],
    ['a 404 (stale run)', () => new ApiError('HTTP 404  for /x', { status: 404 })],
  ])('%s keeps the existing contract: the scenario preview is shown', async (_label, failure) => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    scope = effectScope()
    const app = scope.run(() => useSimulatorApp())!
    await settle()
    expect(app.state.snapshot?.nodes.map((n) => n.id)).toEqual(['RUN_NODE'])

    vi.mocked(getSnapshot).mockRejectedValue(failure())
    await app.admin.attachRun('R')
    await settle()

    expect(app.state.snapshot?.nodes.map((n) => n.id)).toEqual(['PREVIEW_NODE'])
  })
})
