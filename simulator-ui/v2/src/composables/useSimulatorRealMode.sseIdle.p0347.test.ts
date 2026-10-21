/**
 * BACKLOG 034-7 - reproducer at the level the visitor sees: the stream state of a run (`real.sseState`, which the top
 * bar turns into "нет связи" when it is `reconnecting`, `TopBar.vue` `streamLost`), with the REAL transport
 * (`api/sse.ts`) over a stubbed `fetch` and a controlled clock.
 *
 * Target behaviour, RED before the fix: a stream that delivers its bootstrap and then hangs without EOF stays `open`
 * for ever - the network looks alive. After the fix, past the idle bound the stream is reported lost through the
 * SAME path a network error takes (error line, `reconnecting`, the back-off ladder) and the reconnect carries the
 * replay cursor of the last accepted frame (`Last-Event-ID`). Counter-check: a healthy stream that sends only the
 * server's keep-alive comments (every 15 s, `app/api/v1/simulator.py` `keepalive_sec`) stays `open` with one
 * connection for ten minutes.
 *
 * The bound is not imported: any idle bound in (15 s, 58 s] satisfies the test.
 */
import { computed, nextTick, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useSimulatorRealMode, type RealModeState } from './useSimulatorRealMode'

vi.mock('../api/simulatorApi', () => {
  return {
    artifactDownloadUrl: () => 'http://artifact',
    createRun: vi.fn(async () => ({ run_id: 'r1' })),
    getActiveRun: vi.fn(async () => ({ run_id: 'r1' })),
    getRun: vi.fn(async () => ({
      run_id: 'r1',
      scenario_id: 'sc1',
      state: 'running',
      sim_time_ms: 0,
      intensity_percent: 0,
      ops_sec: 0,
      queue_depth: 0,
      last_event_type: null,
      current_phase: null,
      last_error: null,
    })),
    listArtifacts: vi.fn(async () => ({ items: [] })),
    listScenarios: vi.fn(async () => ({ items: [] })),
    pauseRun: vi.fn(async () => undefined),
    resumeRun: vi.fn(async () => undefined),
    setIntensity: vi.fn(async () => undefined),
    stopRun: vi.fn(async () => undefined),
  }
})

vi.mock('../api/equivalentsApi', () => {
  return { fetchEquivalentPrecisions: vi.fn(async () => []) }
})

const SERVER_KEEPALIVE_MS = 15_000
/** The window in which the idle bound and the first step of the reconnect ladder (1 s +- 10 %) must both have run. */
const IDLE_UPPER_MS = 60_000
/** The top step of the reconnect ladder plus its jitter (`useSimulatorRealMode.ts`, `SSE_BACKOFF_STEPS_MS`). */
const BACKOFF_UPPER_MS = 25_000

type Conn = {
  lastEventId: string | null
  push: (text: string) => void
  cancelled: () => boolean
}

/** Every `/events` request gets its own hand-held stream; the test reads them in order. */
function stubSseFetch() {
  const conns: Conn[] = []
  const enc = new TextEncoder()
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: unknown, init?: RequestInit) => {
      if (!String(url).includes('/events')) return new Response('{}', { status: 200 })
      let ctl!: ReadableStreamDefaultController<Uint8Array>
      let cancelled = false
      const stream = new ReadableStream<Uint8Array>({
        start(c) {
          ctl = c
        },
        cancel() {
          cancelled = true
        },
      })
      conns.push({
        lastEventId: new Headers(init?.headers).get('Last-Event-ID'),
        push: (text) => ctl.enqueue(enc.encode(text)),
        cancelled: () => cancelled,
      })
      return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    }),
  )
  return conns
}

function frame(eventId: string, payload: Record<string, unknown>): string {
  return `id: ${eventId}\nevent: simulator.event\ndata: ${JSON.stringify({ event_id: eventId, ...payload })}\n\n`
}

const BOOTSTRAP = frame('evt_r1_000001', {
  ts: '2026-01-01T00:00:00Z',
  type: 'run_status',
  run_id: 'r1',
  scenario_id: 'sc1',
  state: 'running',
  attempts_total: 0,
  committed_total: 0,
  rejected_total: 0,
  errors_total: 0,
  timeouts_total: 0,
  last_error: null,
})

function createHarness() {
  const isRealModeRef = ref(false)
  const real = reactive<RealModeState>({
    apiBase: 'http://x',
    accessToken: 't',
    loadingScenarios: false,
    scenarios: [],
    selectedScenarioId: 'sc1',
    intensityPercent: 0,
    runId: 'r1',
    runStatus: null,
    sseState: 'idle',
    lastEventId: null,
    lastError: '',
    artifacts: [],
    artifactsLoading: false,
    runStats: {
      startedAtMs: 0,
      attempts: 0,
      committed: 0,
      rejected: 0,
      errors: 0,
      timeouts: 0,
      rejectedByCode: {},
      errorsByCode: {},
    },
  })
  const h = useSimulatorRealMode({
    isRealMode: computed(() => isRealModeRef.value),
    isLocalhost: false,
    effectiveEq: computed(() => 'EUR'),
    state: { loading: false, error: '', sourcePath: '', snapshot: null, selectedNodeId: null },
    real,
    ensureScenarioSelectionValid: () => undefined,
    resetRunStats: () => undefined,
    cleanupRealRunFxAndTimers: () => undefined,
    isUserFacingRunError: () => false,
    inc: () => undefined,
    loadScene: async () => undefined,
    loadRecoveryScene: async () => true,
    realPatchApplier: { applyNodePatches: () => undefined, applyEdgePatches: () => undefined, applyNodeStatus: () => undefined, applyEdgeStatus: () => undefined },
    pushTxAmountLabel: () => undefined,
    routeArrivalMs: () => 1200,
    scheduleTimeout: () => undefined,
    runRealTxFx: () => undefined,
    runRealClearingDoneFx: () => undefined,
    wakeUp: () => undefined,
    onAnySseEvent: () => undefined,
  })
  return { h, isRealModeRef, real }
}

let randomSpy: ReturnType<typeof vi.spyOn> | null = null

beforeEach(() => {
  vi.useFakeTimers()
  randomSpy = vi.spyOn(Math, 'random').mockReturnValue(0.5)
})

afterEach(() => {
  randomSpy?.mockRestore()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('034-7: a run stream that hangs without EOF', () => {
  it('is reported lost after the idle bound and reconnects with the replay cursor', async () => {
    const conns = stubSseFetch()
    const { h, isRealModeRef, real } = createHarness()
    try {
      isRealModeRef.value = true
      await nextTick()
      await vi.advanceTimersByTimeAsync(0)
      await vi.waitFor(() => expect(conns.length).toBe(1))

      conns[0]!.push(BOOTSTRAP)
      await vi.advanceTimersByTimeAsync(0)
      expect(real.sseState, 'the bootstrap frame did not open the stream: the test drives nothing').toBe('open')
      expect(real.lastEventId).toBe('evt_r1_000001')

      // Silence: no byte, no EOF.
      const before = Date.now()
      await vi.advanceTimersByTimeAsync(IDLE_UPPER_MS)
      expect(Date.now() - before, 'the fake clock did not move').toBe(IDLE_UPPER_MS)

      expect(real.sseState, 'a stream silent past the idle bound still reads as a live network').toBe('reconnecting')
      expect(real.lastErrorLine, 'the loss did not go through the existing failure line').toMatchObject({ kind: 'dict', key: 'failure.noAnswer' })
      expect(conns[0]!.cancelled(), 'the silent connection was not released').toBe(true)

      // The first step of the ladder (1 s) fits in the same window; the new connection's own connect bound has not run.
      expect(conns.length, 'no reconnect after the stream was reported lost').toBe(2)
      expect(conns[1]!.lastEventId, 'the reconnect lost the replay cursor').toBe('evt_r1_000001')

      // The new connection is a normal one: its bootstrap opens the stream again.
      conns[1]!.push(frame('evt_r1_000002', { ts: '2026-01-01T00:00:01Z', type: 'run_status', run_id: 'r1', scenario_id: 'sc1', state: 'running', attempts_total: 0, committed_total: 0, rejected_total: 0, errors_total: 0, timeouts_total: 0, last_error: null }))
      await vi.advanceTimersByTimeAsync(0)
      expect(real.sseState).toBe('open')
    } finally {
      h.stopSse()
    }
  })

  it('COUNTER-CHECK: a healthy stream with keep-alives every 15 s stays open on one connection for ten minutes', async () => {
    const conns = stubSseFetch()
    const { h, isRealModeRef, real } = createHarness()
    try {
      isRealModeRef.value = true
      await nextTick()
      await vi.advanceTimersByTimeAsync(0)
      await vi.waitFor(() => expect(conns.length).toBe(1))
      conns[0]!.push(BOOTSTRAP)
      await vi.advanceTimersByTimeAsync(0)
      expect(real.sseState).toBe('open')

      for (let i = 0; i < 40; i += 1) {
        await vi.advanceTimersByTimeAsync(SERVER_KEEPALIVE_MS)
        conns[0]!.push(': keep-alive\n\n')
        await vi.advanceTimersByTimeAsync(0)
      }

      expect(real.sseState, 'a healthy stream (keep-alive every 15 s) was reported lost').toBe('open')
      expect(conns.length, 'a healthy stream was reconnected').toBe(1)
      expect(real.lastError).toBe('')
    } finally {
      h.stopSse()
    }
  })

  it('stopSse while the stream is silent leaves no transport timer behind', async () => {
    const conns = stubSseFetch()
    const { h, isRealModeRef, real } = createHarness()
    isRealModeRef.value = true
    await nextTick()
    await vi.advanceTimersByTimeAsync(0)
    await vi.waitFor(() => expect(conns.length).toBe(1))
    conns[0]!.push(BOOTSTRAP)
    await vi.advanceTimersByTimeAsync(0)
    expect(real.sseState).toBe('open')

    h.stopSse()
    isRealModeRef.value = false
    await nextTick()
    await vi.advanceTimersByTimeAsync(0)
    // Counted before the idle bound could fire: a leaked transport timer is still pending here. (After stopSse the
    // bound is already settled, so a leaked timer firing later would do nothing visible.)
    expect(vi.getTimerCount(), 'a timer was left behind after stopSse').toBe(0)
    await vi.advanceTimersByTimeAsync(IDLE_UPPER_MS + BACKOFF_UPPER_MS)

    expect(conns.length, 'a stopped stream reconnected').toBe(1)
    expect(real.lastError, 'a stopped stream reported a timeout').toBe('')
  })
})
