/**
 * 054-B-7c (the owner's decision of 2026-10-30; adapted from the withdrawn 054-A-7 guards, `b6ddeb61`). A run the server
 * stopped by one of its guards (`REAL_MODE_TOO_MANY_ERRORS`, `REAL_MODE_TOO_MANY_TIMEOUTS`, `REAL_MODE_TICK_FAILED_REPEATED`,
 * `REAL_MODE_MONEY_NO_PROGRESS`, `INTEGRITY_VIOLATION`) is not resumed; the server (stream A) answers such a resume with 409.
 *   - the `error` status puts the run's ended-by-error line on screen with its code and message in the text (054-B-7b);
 *   - a resume refused with 409 is a failure of the request: the run is KEPT (a 409 is not "the run is gone", OMR-4 /
 *     054-B-1), the line says the server could not complete it; a 404 is unchanged - the line says "not found", the run is
 *     kept on this path too (the resume path never reset a run; a gone run is reset by the status read, the stream and
 *     the snapshot).
 * The resume path itself is unchanged by 054-B-7c: these cases pin it against the new 409 (green before - guards, with
 * a mutation check of their teeth recorded in the 054-B-7c report).
 * Actual useSimulatorRealMode, REAL SSE transport (api/sse.ts) over a stubbed fetch; API functions mocked at the module
 * boundary.
 */
import { computed, nextTick, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/http'
import { getRun, resumeRun } from '../api/simulatorApi'
import { renderRunErrorLine, runErrorLineTitle } from '../utils/runErrorClassification'
import { useSimulatorRealMode, type RealModeState } from './useSimulatorRealMode'

const GUARD_CODES = [
  'REAL_MODE_TOO_MANY_ERRORS',
  'REAL_MODE_TOO_MANY_TIMEOUTS',
  'REAL_MODE_TICK_FAILED_REPEATED',
  'REAL_MODE_MONEY_NO_PROGRESS',
  'INTEGRITY_VIOLATION',
] as const

const lastErrorOf = (code: string) => ({ code, message: `${code} message`, at: '2026-10-30T00:00:00Z' })
const statusOf = (state: string, lastError: ReturnType<typeof lastErrorOf> | null = null) => ({
  run_id: 'r1', scenario_id: 'sc1', state, sim_time_ms: 0, intensity_percent: 30, ops_sec: 0, queue_depth: 0,
  last_event_type: null, current_phase: null, last_error: lastError,
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
const statusFrame = (state: string, lastError: ReturnType<typeof lastErrorOf> | null = null) => {
  const id = `evt_r1_${String(++n).padStart(6, '0')}`
  return `id: ${id}\nevent: simulator.event\ndata: ${JSON.stringify({ event_id: id, ts: '2026-10-30T00:00:00Z', type: 'run_status', run_id: 'r1', scenario_id: 'sc1', state, last_error: lastError })}\n\n`
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

/** Boots onto a running run with its stream open. */
async function bootRunning() {
  const conns = stubSseFetch()
  vi.mocked(getRun).mockResolvedValue(statusOf('running') as never)
  const hr = harness()
  hr.isRealModeRef.value = true
  await nextTick(); await vi.advanceTimersByTimeAsync(0)
  await vi.waitFor(() => expect(conns.length).toBe(1))
  conns[0]!.push(statusFrame('running')); await vi.advanceTimersByTimeAsync(0)
  expect(hr.real.runStatus?.state, 'stand: boot running').toBe('running')
  return { ...hr, conns }
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.spyOn(Math, 'random').mockReturnValue(0.5)
  vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('054-B-7c: a run stopped by the guards - its line, and a refused resume keeps the run', () => {
  for (const code of GUARD_CODES) {
    it(`${code}: the ended-by-error line is on screen with the code and the message in the text`, async () => {
      const { real, conns } = await bootRunning()
      vi.mocked(getRun).mockResolvedValue(statusOf('error', lastErrorOf(code)) as never)
      conns[0]!.push(statusFrame('error', lastErrorOf(code))); await vi.advanceTimersByTimeAsync(0)

      expect(real.runStatus?.state).toBe('error')
      expect(real.lastErrorLine, 'the line of a run ended by an error').toMatchObject({ kind: 'code', key: 'runLine.errorStopped', code })
      expect(real.lastError, 'the sentence on screen').toBe(renderRunErrorLine(real.lastErrorLine!))
      expect(real.lastError, 'the code and the message are in the text').toContain(`${code} — ${code} message`)
      expect(runErrorLineTitle(real.lastErrorLine), 'and behind the line').toBe(`${code}: ${code} message`)
    })

    it(`${code}: a resume refused with 409 keeps the run and says the server could not complete it`, async () => {
      const { h, real, conns } = await bootRunning()
      conns[0]!.push(statusFrame('error', lastErrorOf(code))); await vi.advanceTimersByTimeAsync(0)
      vi.mocked(resumeRun).mockRejectedValue(new ApiError('HTTP 409 Conflict for /simulator/runs/r1/resume', { status: 409 }))

      await h.resume()
      expect(real.runId, 'a 409 is not "the run is gone": the run is kept').toBe('r1')
      expect(real.runStatus?.state, 'the run status is not reset').toBe('error')
      expect(real.lastErrorLine, 'the line is the refused request').toMatchObject({ kind: 'dict', key: 'failure.serverFailed' })
      expect(runErrorLineTitle(real.lastErrorLine)).toContain('409')
    })
  }

  it('counter-check: a resume answered 404 behaves as before - the "not found" line, the run kept on this path', async () => {
    const { h, real } = await bootRunning()
    real.runStatus = statusOf('paused') as RealModeState['runStatus']
    vi.mocked(resumeRun).mockRejectedValue(new ApiError('HTTP 404 Not Found for /simulator/runs/r1/resume', { status: 404 }))

    await h.resume()
    expect(real.runId).toBe('r1')
    expect(real.lastErrorLine).toMatchObject({ kind: 'dict', key: 'failure.notFound' })
  })
})
