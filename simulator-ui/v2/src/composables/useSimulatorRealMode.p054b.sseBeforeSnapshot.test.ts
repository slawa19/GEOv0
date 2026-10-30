/**
 * 054-B-2 (P2, AGENTS.md §8 Simulator SSE): on a change of run the event stream is opened only AFTER the run's snapshot
 * has been answered - for every owner of a change of run: the opening's discovery of an active run, a start (created,
 * or attached to the active run on a 409) and the attach by id. Before 054-B-2 the run-context
 * watcher opened the stream the moment `real.runId` changed, while the owner of the change was still reading the run's
 * status and snapshot: events were applied to the old scene and the cursor moved past them before the snapshot replaced
 * it (`useSimulatorApp.p054b.sseBeforeSnapshot.test.ts` shows the lost payment end to end).
 * Actual useSimulatorRealMode, REAL SSE transport (api/sse.ts) over a stubbed fetch, API functions mocked at the module
 * boundary, the scene load held.
 */
import { computed, nextTick, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/http'
import { createRun, getActiveRun, getRun } from '../api/simulatorApi'
import { useSimulatorRealMode, type RealModeState } from './useSimulatorRealMode'

const statusOf = (runId: string, state = 'running') => ({
  run_id: runId, scenario_id: 'sc1', state, sim_time_ms: 0, intensity_percent: 30, ops_sec: 0, queue_depth: 0,
  last_event_type: null, current_phase: null, last_error: null,
})

vi.mock('../api/simulatorApi', () => ({
  artifactDownloadUrl: () => 'http://artifact',
  createRun: vi.fn(),
  ensureSession: vi.fn(async () => ({ actor_kind: 'anon', owner_id: 'o1' })),
  getActiveRun: vi.fn(),
  getRun: vi.fn(),
  listArtifacts: vi.fn(async () => ({ items: [] })),
  listScenarios: vi.fn(async () => ({ items: [{ scenario_id: 'sc1' }] })),
  pauseRun: vi.fn(),
  resumeRun: vi.fn(),
  setIntensity: vi.fn(async () => undefined),
  stopRun: vi.fn(async () => undefined),
}))
vi.mock('../api/equivalentsApi', () => ({ fetchEquivalentPrecisions: vi.fn(async () => []) }))

type Conn = { url: string; lastEventId: string | null }
function stubSseFetch() {
  const conns: Conn[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: unknown, init?: RequestInit) => {
    if (!String(url).includes('/events')) return new Response('{}', { status: 200 })
    conns.push({ url: String(url), lastEventId: new Headers(init?.headers).get('Last-Event-ID') })
    const stream = new ReadableStream<Uint8Array>({ start() { /* held open */ } })
    return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
  }))
  return conns
}

function harness(initialRunId: string | null) {
  const isRealModeRef = ref(false)
  const real = reactive<RealModeState>({
    apiBase: 'http://x', accessToken: 't', loadingScenarios: false, scenarios: [], selectedScenarioId: 'sc1',
    intensityPercent: 30, runId: initialRunId, runStatus: null, sseState: 'idle', lastEventId: null, lastError: '',
    artifacts: [], artifactsLoading: false,
    runStats: { startedAtMs: 0, attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} },
  })
  // Every scene load is held until the test answers it; `loads` records which run each one was asked for.
  const loads: Array<{ runId: string | null; answer: () => void }> = []
  const h = useSimulatorRealMode({
    isRealMode: computed(() => isRealModeRef.value), isLocalhost: false, effectiveEq: computed(() => 'UAH'),
    state: { loading: false, error: '', sourcePath: '', snapshot: null, selectedNodeId: null }, real,
    ensureScenarioSelectionValid: () => undefined, resetRunStats: () => undefined, cleanupRealRunFxAndTimers: () => undefined,
    isUserFacingRunError: () => false, inc: () => undefined,
    loadScene: () => new Promise<void>((resolve) => { loads.push({ runId: real.runId, answer: resolve }) }),
    loadRecoveryScene: async () => true,
    realPatchApplier: { applyNodePatches: () => undefined, applyEdgePatches: () => undefined, applyNodeStatus: () => undefined, applyEdgeStatus: () => undefined },
    pushTxAmountLabel: () => undefined, routeArrivalMs: () => 0, scheduleTimeout: () => undefined,
    runRealTxFx: () => undefined, runRealClearingDoneFx: () => undefined, wakeUp: () => undefined, onAnySseEvent: () => undefined,
  })
  return { h, isRealModeRef, real, loads }
}

async function settle() {
  for (let i = 0; i < 30; i += 1) { await Promise.resolve(); await nextTick() }
  await vi.advanceTimersByTimeAsync(200)
}

/** Answers every held scene load and lets the owner go on. */
async function answerLoads(loads: Array<{ answer: () => void }>) {
  for (const l of loads.splice(0)) l.answer()
  await settle()
}

const streamsOf = (conns: Conn[], runId: string) => conns.filter((c) => c.url.includes(`/runs/${runId}/events`))

beforeEach(() => {
  vi.useFakeTimers()
  vi.spyOn(Math, 'random').mockReturnValue(0.5)
  vi.mocked(getRun).mockImplementation((async (_c: unknown, id: string) => statusOf(id)) as never)
  vi.mocked(getActiveRun).mockResolvedValue({ run_id: null } as never)
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('054-B-2: the stream of a new run opens only after its snapshot', () => {
  it('the opening discovers an active run: no stream before the snapshot answers', async () => {
    const conns = stubSseFetch()
    vi.mocked(getActiveRun).mockResolvedValue({ run_id: 'r1' } as never)
    const { isRealModeRef, real, loads } = harness(null)
    isRealModeRef.value = true
    await settle()

    expect(real.runId, 'stand: the run was discovered').toBe('r1')
    expect(loads.map((l) => l.runId), 'stand: the snapshot of the discovered run is in flight').toContain('r1')
    expect(conns.length, 'no event stream before the snapshot of the discovered run answered').toBe(0)

    await answerLoads(loads)
    expect(streamsOf(conns, 'r1').length, 'the stream opens after the snapshot').toBe(1)
    expect(conns.length).toBe(1)
  })

  it('a start that creates a run: no stream before the snapshot answers', async () => {
    const conns = stubSseFetch()
    vi.mocked(createRun).mockResolvedValue({ run_id: 'r2' } as never)
    const { h, isRealModeRef, real, loads } = harness(null)
    isRealModeRef.value = true
    await settle()
    await answerLoads(loads) // the opening's scenario preview
    expect(conns.length, 'stand: no run yet, no stream').toBe(0)

    const started = h.startRun()
    await settle()
    expect(real.runId, 'stand: the run was created').toBe('r2')
    expect(loads.map((l) => l.runId), 'stand: the snapshot of the created run is in flight').toContain('r2')
    expect(conns.length, 'no event stream before the snapshot of the created run answered').toBe(0)

    await answerLoads(loads)
    await started
    expect(streamsOf(conns, 'r2').length, 'the stream opens after the snapshot').toBe(1)
    expect(conns.length).toBe(1)
  })

  it('a start refused with 409 attaches to the active run: no stream before the snapshot answers', async () => {
    const conns = stubSseFetch()
    vi.mocked(createRun).mockRejectedValue(new ApiError('HTTP 409 Conflict for /simulator/runs', { status: 409 }))
    const { h, isRealModeRef, real, loads } = harness(null)
    isRealModeRef.value = true
    await settle()
    await answerLoads(loads)
    vi.mocked(getActiveRun).mockResolvedValue({ run_id: 'r3' } as never)

    const started = h.startRun()
    await settle()
    expect(real.runId, 'stand: the start attached to the active run').toBe('r3')
    expect(loads.map((l) => l.runId), 'stand: the snapshot of the attached run is in flight').toContain('r3')
    expect(conns.length, 'no event stream before the snapshot of the attached run answered').toBe(0)

    await answerLoads(loads)
    await started
    expect(streamsOf(conns, 'r3').length, 'the stream opens after the snapshot').toBe(1)
    expect(conns.length).toBe(1)
  })

  it('the attach by id: no stream before the snapshot answers', async () => {
    const conns = stubSseFetch()
    const { h, isRealModeRef, real, loads } = harness(null)
    isRealModeRef.value = true
    await settle()
    await answerLoads(loads)

    const attached = h.attachToRun('r4')
    await settle()
    expect(real.runId).toBe('r4')
    expect(loads.map((l) => l.runId), 'stand: the snapshot of the attached run is in flight').toContain('r4')
    expect(conns.length, 'no event stream before the snapshot of the attached run answered').toBe(0)

    await answerLoads(loads)
    await attached
    expect(streamsOf(conns, 'r4').length, 'the stream opens after the snapshot').toBe(1)
  })
})
