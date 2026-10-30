/**
 * 054-B-4 (P3): the run's control commands are answered in the order they were asked. Before 054-B-4 a late HTTP answer
 * of Resume overwrote a pause confirmed since then (`pauseRun`/`resumeRun` results were assigned unconditionally), and the
 * server publishes no status of a paused run that would correct it.
 * Actual useSimulatorRealMode, REAL SSE transport (api/sse.ts) over a stubbed fetch; API functions mocked at the module
 * boundary with held answers.
 */
import { computed, nextTick, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/http'
import { pauseRun, resumeRun, getRun, setIntensity } from '../api/simulatorApi'
import { useSimulatorRealMode, type RealModeState } from './useSimulatorRealMode'

const statusOf = (state: string) => ({
  run_id: 'r1', scenario_id: 'sc1', state, sim_time_ms: 0, intensity_percent: 30, ops_sec: 0, queue_depth: 0,
  last_event_type: null, current_phase: null, last_error: null,
})

vi.mock('../api/simulatorApi', () => ({
  artifactDownloadUrl: () => 'http://artifact',
  createRun: vi.fn(),
  ensureSession: vi.fn(async () => ({ actor_kind: 'anon', owner_id: 'o1' })),
  getActiveRun: vi.fn(async () => ({ run_id: 'r1' })),
  getRun: vi.fn(),
  listArtifacts: vi.fn(async () => ({ items: [] })),
  listScenarios: vi.fn(async () => ({ items: [] })),
  pauseRun: vi.fn(),
  resumeRun: vi.fn(),
  setIntensity: vi.fn(async () => undefined),
  stopRun: vi.fn(async () => undefined),
}))
vi.mock('../api/equivalentsApi', () => ({ fetchEquivalentPrecisions: vi.fn(async () => []) }))

type Conn = { push: (text: string) => void }
function stubSseFetch() {
  const conns: Conn[] = []
  const enc = new TextEncoder()
  vi.stubGlobal('fetch', vi.fn(async (url: unknown) => {
    if (!String(url).includes('/events')) return new Response('{}', { status: 200 })
    let ctl!: ReadableStreamDefaultController<Uint8Array>
    const stream = new ReadableStream<Uint8Array>({ start(c) { ctl = c } })
    conns.push({ push: (t) => ctl.enqueue(enc.encode(t)) })
    return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
  }))
  return conns
}
let n = 0
const statusFrame = (state: string) => {
  const id = `evt_r1_${String(++n).padStart(6, '0')}`
  return `id: ${id}\nevent: simulator.event\ndata: ${JSON.stringify({ event_id: id, ts: '2026-01-01T00:00:00Z', type: 'run_status', run_id: 'r1', scenario_id: 'sc1', state, attempts_total: 0, committed_total: 0, rejected_total: 0, errors_total: 0, timeouts_total: 0, last_error: null })}\n\n`
}

function harness() {
  const isRealModeRef = ref(false)
  const real = reactive<RealModeState>({
    apiBase: 'http://x', accessToken: 't', loadingScenarios: false, scenarios: [], selectedScenarioId: 'sc1',
    intensityPercent: 30, runId: 'r1', runStatus: null, sseState: 'idle', lastEventId: null, lastError: '',
    artifacts: [], artifactsLoading: false,
    runStats: { startedAtMs: 0, attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} },
  })
  const h = useSimulatorRealMode({
    isRealMode: computed(() => isRealModeRef.value), isLocalhost: false, effectiveEq: computed(() => 'UAH'),
    state: { loading: false, error: '', sourcePath: '', snapshot: null, selectedNodeId: null }, real,
    ensureScenarioSelectionValid: () => undefined, resetRunStats: () => undefined, cleanupRealRunFxAndTimers: () => undefined,
    isUserFacingRunError: () => false, inc: () => undefined, loadScene: async () => undefined, loadRecoveryScene: async () => true,
    realPatchApplier: { applyNodePatches: () => undefined, applyEdgePatches: () => undefined, applyNodeStatus: () => undefined, applyEdgeStatus: () => undefined },
    pushTxAmountLabel: () => undefined, routeArrivalMs: () => 0, scheduleTimeout: () => undefined,
    runRealTxFx: () => undefined, runRealClearingDoneFx: () => undefined, wakeUp: () => undefined, onAnySseEvent: () => undefined,
  })
  return { h, isRealModeRef, real }
}

beforeEach(() => { vi.useFakeTimers(); vi.spyOn(Math, 'random').mockReturnValue(0.5) })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('054-B-4: a late answer of an older control command is not applied', () => {
  it('Resume (answer held) -> SSE running -> Pause (ok) -> SSE paused -> late Resume answer: UI must stay paused', async () => {
    const conns = stubSseFetch()
    vi.mocked(getRun).mockResolvedValue(statusOf('paused') as never)
    let releaseResume!: () => void
    vi.mocked(resumeRun).mockImplementation(() => new Promise((res) => { releaseResume = () => res(statusOf('running') as never) }))
    vi.mocked(pauseRun).mockResolvedValue(statusOf('paused') as never)

    const { h, isRealModeRef, real } = harness()
    isRealModeRef.value = true
    await nextTick(); await vi.advanceTimersByTimeAsync(0)
    await vi.waitFor(() => expect(conns.length).toBe(1))
    conns[0]!.push(statusFrame('paused')); await vi.advanceTimersByTimeAsync(0)
    expect(real.runStatus?.state, 'stand: boot paused').toBe('paused')

    const resumeP = h.resume()                       // click "Поток": HTTP answer held
    conns[0]!.push(statusFrame('running')); await vi.advanceTimersByTimeAsync(0)
    expect(real.runStatus?.state, 'stand: SSE says running, button shows pause').toBe('running')

    await h.pause()                                   // second click: pause confirmed by HTTP
    conns[0]!.push(statusFrame('paused')); await vi.advanceTimersByTimeAsync(0)
    expect(real.runStatus?.state, 'stand: pause confirmed by HTTP and SSE').toBe('paused')

    releaseResume(); await resumeP                    // the old Resume answer lands now
    await vi.advanceTimersByTimeAsync(30_000)         // keep-alive only: nothing corrects it
    expect(real.runStatus?.state, 'the server is paused; the late Resume answer must not win').toBe('paused')
  })

  /** Boots the harness onto a paused run with one open stream; returns it with the stream. */
  async function bootPaused() {
    const conns = stubSseFetch()
    vi.mocked(getRun).mockResolvedValue(statusOf('paused') as never)
    const hr = harness()
    hr.isRealModeRef.value = true
    await nextTick(); await vi.advanceTimersByTimeAsync(0)
    await vi.waitFor(() => expect(conns.length).toBe(1))
    conns[0]!.push(statusFrame('paused')); await vi.advanceTimersByTimeAsync(0)
    expect(hr.real.runStatus?.state, 'stand: boot paused').toBe('paused')
    return { ...hr, conns }
  }
  const lateAnswer = (state: string) => ({ ...statusOf(state), sim_time_ms: 999_999 })

  it('a later command that FAILS does not let the older answer through', async () => {
    const { h, real, conns } = await bootPaused()
    let releaseResume!: () => void
    vi.mocked(resumeRun).mockImplementation(() => new Promise((res) => { releaseResume = () => res(lateAnswer('running') as never) }))
    vi.mocked(pauseRun).mockRejectedValue(new ApiError('HTTP 503 Service Unavailable for /simulator/runs/r1/pause', { status: 503 }))

    const resumeP = h.resume()
    conns[0]!.push(statusFrame('running')); await vi.advanceTimersByTimeAsync(0)
    await h.pause() // fails: its failure is the newest thing the UI was told
    expect(real.lastError, 'stand: the failed pause is on the error line').not.toBe('')
    const errorAfterPause = real.lastError

    releaseResume(); await resumeP
    expect(real.runStatus?.sim_time_ms, 'the older Resume answer is not applied after the newer command failed').not.toBe(999_999)
    expect(real.lastError, 'the failed pause stays on the line').toBe(errorAfterPause)
  })

  it('a late FAILURE of an older command is not written over the newer answer', async () => {
    const { h, real, conns } = await bootPaused()
    let failResume!: () => void
    vi.mocked(resumeRun).mockImplementation(() => new Promise((_res, rej) => {
      failResume = () => rej(new ApiError('HTTP 503 Service Unavailable for /simulator/runs/r1/resume', { status: 503 }))
    }))
    vi.mocked(pauseRun).mockResolvedValue(statusOf('paused') as never)

    const resumeP = h.resume()
    conns[0]!.push(statusFrame('running')); await vi.advanceTimersByTimeAsync(0)
    await h.pause()
    expect(real.runStatus?.state, 'stand: pause confirmed').toBe('paused')

    failResume(); await resumeP
    expect(real.lastError, 'a failure of the superseded Resume is not shown').toBe('')
    expect(real.runStatus?.state).toBe('paused')
  })

  it('a late answer of the tempo is not applied over a pause asked after it', async () => {
    const { h, real } = await bootPaused()
    real.runStatus = statusOf('running') as RealModeState['runStatus']
    let releaseTempo!: () => void
    vi.mocked(setIntensity).mockImplementation((() => new Promise((res) => { releaseTempo = () => res(lateAnswer('running') as never) })) as never)
    vi.mocked(pauseRun).mockResolvedValue(statusOf('paused') as never)

    const tempoP = h.applyIntensity()
    await h.pause()
    expect(real.runStatus?.state, 'stand: pause confirmed').toBe('paused')

    releaseTempo(); await tempoP
    expect(real.runStatus?.state, 'the late tempo answer does not resume the run on screen').toBe('paused')
  })

  it('counter-check: the answer of the latest command is applied', async () => {
    const { h, real } = await bootPaused()
    vi.mocked(resumeRun).mockResolvedValue(lateAnswer('running') as never)
    await h.resume()
    expect(real.runStatus?.state, 'the one command asked is applied').toBe('running')
    expect(real.runStatus?.sim_time_ms).toBe(999_999)
  })
})
