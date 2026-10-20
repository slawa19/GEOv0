/**
 * 049 decision 5 (closing Codex review of PR #341, P2-1): the history belongs to the picture on screen. A run stopped,
 * then another scenario chosen: its preview replaces the run's picture while the run id and the equivalent stay - and
 * the old run's lines stood over scenario B. The signal is the source of the scene on screen (`state.sourcePath`, set when
 * the scene owner accepts a snapshot); another source clears the history, the same source loaded again does not.
 *
 * Through the real entrances of `useSimulatorApp` (boot, Stop, the scenario choice, a refresh), as in
 * `useSimulatorApp.sceneLoadFailure.p034.test.ts`. The own line is put into the history directly: how it is written is
 * `useInteractMode`'s (`eventFeed.p049s2.test.ts`), what is judged here is when it is dropped.
 */
import { effectScope, nextTick, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createRun, getActiveRun, getRun, getScenarioPreview, getSnapshot, listScenarios, stopRun } from '../api/simulatorApi'
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
    stopRun: vi.fn(),
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

const snap = (nodeId: string, equivalent = 'UAH') =>
  ({ equivalent, generated_at: '2026-10-21T00:00:00Z', nodes: [{ id: nodeId, name: nodeId, type: 'person', status: 'active' }], links: [] }) as unknown as SimulatorGraphSnapshot

const status = (state: string) => ({
  run_id: 'R', scenario_id: 'sc1', state, api_version: 'simulator-api/1', mode: 'real', sim_time_ms: 0, intensity_percent: 5,
  ops_sec: 0, queue_depth: 0, last_event_type: null, current_phase: null, last_error: null,
})

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

let scope: EffectScope | null = null
let runState = 'running'

function boot() {
  scope = effectScope()
  return scope.run(() => useSimulatorApp())!
}

/** An own line of the run on screen (the shape `useInteractHistory` writes). */
function ownLine(app: ReturnType<typeof useSimulatorApp>) {
  app.interact.mode.history.push({
    id: 1, icon: '💸', line: { key: 'history.payment', vars: { amount: '5.00', unit: 'UAH', from: 'A', to: 'B' } },
    timeText: '00:00:00', timestampMs: 0, seq: 1, source: 'own',
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  runState = 'running'
  vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
  vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  window.history.replaceState({}, '', '/?mode=real')
  vi.mocked(listScenarios).mockResolvedValue({
    api_version: 'simulator-api/1',
    items: [{ scenario_id: 'sc1', label: 'Scenario 1' }, { scenario_id: 'sc2', label: 'Scenario 2' }],
  } as never)
  vi.mocked(getActiveRun).mockResolvedValue({ run_id: 'R' } as never)
  vi.mocked(getRun).mockImplementation((async () => status(runState)) as never)
  vi.mocked(stopRun).mockImplementation((async () => {
    runState = 'stopped'
    return status('stopped')
  }) as never)
  vi.mocked(getSnapshot).mockImplementation((async (_cfg: unknown, runId: string, eq: string) => snap(`${runId}_NODE`, eq)) as never)
  vi.mocked(getScenarioPreview).mockImplementation((async (_cfg: unknown, scenarioId: string) => snap(`PREVIEW_${scenarioId}`)) as never)
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

describe('049 decision 5: the history follows the picture on screen', () => {
  it('a stopped run, then another scenario: its preview replaces the picture - the old run\'s lines are gone', async () => {
    const app = boot()
    await settle()
    expect(app.state.snapshot?.nodes.map((n) => n.id), 'precondition: the run\'s picture is on screen').toEqual(['R_NODE'])
    ownLine(app)
    expect(app.interact.mode.shownHistory.value, 'precondition: the run\'s own line is shown').toHaveLength(1)

    await app.realActions.stop()
    await settle()
    expect(app.real.runId, 'the run id stays after a stop (the clearing by run id cannot see this path)').toBe('R')
    app.realActions.setSelectedScenarioId('sc2')
    await settle()

    expect(app.state.snapshot?.nodes.map((n) => n.id), 'counter-check: scenario B\'s preview is on screen').toEqual(['PREVIEW_sc2'])
    expect(app.real.runId, 'counter-check: the run id did not change').toBe('R')
    expect(app.interact.mode.shownHistory.value.map((e) => e.source), 'the old run\'s lines are not over scenario B').toEqual([])
  })

  it('counter-check: the same run\'s picture loaded again keeps the history - also a full reload (a participant joined)', async () => {
    const app = boot()
    await settle()
    ownLine(app)
    // Another set of nodes: the scene owner reloads in full (`clearScene` empties the source for a moment), same source.
    vi.mocked(getSnapshot).mockImplementation((async (_cfg: unknown, runId: string, eq: string) => ({
      ...snap(`${runId}_NODE`, eq),
      nodes: [{ id: `${runId}_NODE`, name: 'a', type: 'person', status: 'active' }, { id: 'JOINED', name: 'b', type: 'person', status: 'active' }],
    })) as never)
    await app.realActions.refreshSnapshot()
    await settle()
    expect(app.state.snapshot?.nodes.map((n) => n.id), 'counter-check: the run\'s picture was read again, in full').toEqual(['R_NODE', 'JOINED'])
    expect(app.interact.mode.shownHistory.value, 'the same source: the line stays').toHaveLength(1)
  })

  // Argon's closing review of 049 (P2), the two entrances the source alone did not see. The rule: the history follows the
  // IDENTITY of the context on screen - another scenario asked for (shown or failed) or another run - not the source text.
  it('(a) a stopped run, then another scenario whose preview FAILS: the old run\'s lines are not over its error', async () => {
    const app = boot()
    await settle()
    ownLine(app)
    await app.realActions.stop()
    await settle()
    vi.mocked(getScenarioPreview).mockRejectedValueOnce(new Error('500'))
    app.realActions.setSelectedScenarioId('sc2')
    await settle()
    expect(app.state.snapshot, 'counter-check: scenario B failed - no picture').toBeNull()
    expect(app.state.error, 'counter-check: the failure is shown').not.toBe('')
    expect(app.real.runId, 'counter-check: the run id did not change').toBe('R')
    expect(app.interact.mode.shownHistory.value.map((e) => e.source), 'the old run\'s lines are not over scenario B\'s failure').toEqual([])
  })

  it('(b) a run started over the preview, its first picture fails, the next one loads: the run\'s own lines stay', async () => {
    vi.mocked(getActiveRun).mockResolvedValue({ run_id: null } as never)
    let release: (() => void) | null = null
    vi.mocked(createRun).mockImplementation((() => new Promise((resolve) => {
      release = () => resolve({ run_id: 'R1' } as never)
    })) as never)
    vi.mocked(getRun).mockImplementation((async (_cfg: unknown, runId: string) => ({ ...status('running'), run_id: runId })) as never)
    const app = boot()
    await settle()
    expect(app.state.snapshot?.nodes.map((n) => n.id), 'precondition: the preview is on screen, the run not started yet').toEqual(['PREVIEW_sc1'])
    expect(release, 'precondition: the opening asked for a run').not.toBeNull()
    vi.mocked(getSnapshot).mockRejectedValueOnce(new Error('500'))
    release!()
    await settle()
    expect(app.real.runId, 'counter-check: the run started').toBe('R1')
    ownLine(app)
    await app.realActions.refreshSnapshot()
    await settle()
    expect(app.state.snapshot?.nodes.map((n) => n.id), 'counter-check: the run\'s picture replaced the preview').toEqual(['R1_NODE'])
    expect(app.interact.mode.shownHistory.value, 'the same run: its own line stays').toHaveLength(1)
  })
})
