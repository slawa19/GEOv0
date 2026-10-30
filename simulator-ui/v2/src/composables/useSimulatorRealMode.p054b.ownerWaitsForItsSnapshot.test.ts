/**
 * 054-B-2 fix-delta (§15 Codex R1, P2): the owner of a change of run waits for the snapshot of THE NEW RUN before it opens
 * the stream - not for a load that was in flight for the previous context (the scenario preview), and not past a
 * debounce window. Before: `refreshSnapshot()` joined the preview's `refreshSnapshotActivePromise` and returned after it
 * (the new run's load was a `void refreshSnapshot()` from its `finally`, awaited by nobody), or, inside the 80 ms debounce
 * window, returned at once - the stream of the new run opened before its snapshot (Codex: `snapshot end PREVIEW ->
 * snapshot begin NEW -> SSE NEW`).
 * Actual useSimulatorRealMode, REAL SSE transport (api/sse.ts) over a stubbed fetch, API functions mocked at the module
 * boundary, each scene load held or answered by the test.
 */
import { computed, nextTick, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createRun, getActiveRun, getRun } from '../api/simulatorApi'
import { useSimulatorRealMode, type RealModeState } from './useSimulatorRealMode'

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

const statusOf = (runId: string) => ({
  run_id: runId, scenario_id: 'sc1', state: 'running', sim_time_ms: 0, intensity_percent: 30, ops_sec: 0, queue_depth: 0,
  last_event_type: null, current_phase: null, last_error: null,
})

function stubSseFetch() {
  const conns: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: unknown) => {
    if (!String(url).includes('/events')) return new Response('{}', { status: 200 })
    conns.push(String(url))
    return new Response(new ReadableStream<Uint8Array>({ start() { /* held open */ } }), {
      status: 200, headers: { 'Content-Type': 'text/event-stream' },
    })
  }))
  return conns
}

function harness() {
  const isRealModeRef = ref(false)
  const real = reactive<RealModeState>({
    apiBase: 'http://x', accessToken: 't', loadingScenarios: false, scenarios: [], selectedScenarioId: 'sc1',
    intensityPercent: 30, runId: null, runStatus: null, sseState: 'idle', lastEventId: null, lastError: '',
    artifacts: [], artifactsLoading: false,
    runStats: { startedAtMs: 0, attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} },
  })
  // `holdPreview`: a load with no run (the scenario preview) is held too; a load of a run is always held.
  const loads: Array<{ runId: string | null; answer: () => void; answered: boolean }> = []
  let holdPreview = true
  const h = useSimulatorRealMode({
    isRealMode: computed(() => isRealModeRef.value), isLocalhost: false, effectiveEq: computed(() => 'UAH'),
    state: { loading: false, error: '', sourcePath: '', snapshot: null, selectedNodeId: null }, real,
    ensureScenarioSelectionValid: () => undefined, resetRunStats: () => undefined, cleanupRealRunFxAndTimers: () => undefined,
    isUserFacingRunError: () => false, inc: () => undefined,
    loadScene: () => new Promise<void>((resolve) => {
      const load = { runId: real.runId, answered: false, answer: () => { load.answered = true; resolve() } }
      loads.push(load)
      if (!holdPreview && load.runId === null) load.answer()
    }),
    loadRecoveryScene: async () => true,
    realPatchApplier: { applyNodePatches: () => undefined, applyEdgePatches: () => undefined, applyNodeStatus: () => undefined, applyEdgeStatus: () => undefined },
    pushTxAmountLabel: () => undefined, routeArrivalMs: () => 0, scheduleTimeout: () => undefined,
    runRealTxFx: () => undefined, runRealClearingDoneFx: () => undefined, wakeUp: () => undefined, onAnySseEvent: () => undefined,
  })
  return { h, isRealModeRef, real, loads, setHoldPreview: (v: boolean) => { holdPreview = v } }
}

/** Microtasks and Vue flushes only: no timer is advanced (the debounce window stays open). */
async function flush() {
  for (let i = 0; i < 30; i += 1) { await Promise.resolve(); await nextTick() }
}

const pendingOf = <T extends { runId: string | null; answered: boolean }>(loads: T[], runId: string | null): T[] =>
  loads.filter((l) => l.runId === runId && !l.answered)

beforeEach(() => {
  vi.useFakeTimers()
  vi.spyOn(Math, 'random').mockReturnValue(0.5)
  vi.mocked(getRun).mockImplementation((async (_c: unknown, id: string) => statusOf(id)) as never)
  vi.mocked(getActiveRun).mockResolvedValue({ run_id: null } as never)
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('054-B-2 fix-delta: the owner waits for the snapshot of the new run', () => {
  it('a start while the preview is loading: no stream until the NEW run\'s snapshot answered', async () => {
    const conns = stubSseFetch()
    vi.mocked(createRun).mockResolvedValue({ run_id: 'r2' } as never)
    const { h, isRealModeRef, real, loads } = harness()
    isRealModeRef.value = true
    await flush()
    expect(pendingOf(loads, null).length, 'stand: the preview load is in flight').toBe(1)

    const started = h.startRun() // "Старт" while the preview loads
    await flush()
    expect(real.runId, 'stand: the run was created').toBe('r2')

    for (const l of pendingOf(loads, null)) l.answer() // the preview answers; the new run's snapshot does not
    await flush()
    await vi.advanceTimersByTimeAsync(200)
    expect(pendingOf(loads, 'r2').length, 'stand: the snapshot of the new run is asked for and held').toBeGreaterThan(0)
    expect(conns.length, 'no stream before the snapshot of the new run answered').toBe(0)

    for (const l of pendingOf(loads, 'r2')) l.answer()
    await flush()
    await started
    await vi.advanceTimersByTimeAsync(200)
    expect(conns.length, 'the stream opens after it').toBe(1)
  })

  it('a start inside the debounce window of a load that just answered: no stream until the new run\'s snapshot answered', async () => {
    const conns = stubSseFetch()
    let releaseCreate!: () => void
    vi.mocked(createRun).mockImplementation((() => new Promise((res) => { releaseCreate = () => res({ run_id: 'r3' }) })) as never)
    const { h, isRealModeRef, real, loads, setHoldPreview } = harness()
    isRealModeRef.value = true
    await flush()
    for (const l of pendingOf(loads, null)) l.answer() // the opening's preview
    await flush()
    await vi.advanceTimersByTimeAsync(200)
    setHoldPreview(false)

    const started = h.startRun() // the create is held
    await flush()
    // While the create is in flight a refresh of the preview answers at once: its 80 ms debounce window is open.
    await h.refreshSnapshot()
    await flush()
    releaseCreate()
    await flush() // no timer advanced: the window is still open when the owner asks for the new run's snapshot
    expect(real.runId, 'stand: the run was created').toBe('r3')
    expect(conns.length, 'no stream before the snapshot of the new run was even asked for and answered').toBe(0)

    await vi.advanceTimersByTimeAsync(200)
    expect(conns.length, 'no stream while the new run\'s snapshot is held').toBe(0)
    for (const l of pendingOf(loads, 'r3')) l.answer()
    await flush()
    await started
    await vi.advanceTimersByTimeAsync(200)
    expect(conns.length, 'the stream opens after it').toBe(1)
  })
})
