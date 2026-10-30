/**
 * 054-B-7b (the owner's decision of 2026-10-30: «на этапе разработки пользователь и есть администратор; все РЕАЛЬНЫЕ ошибки
 * показывать»): a real failure - infrastructure, integrity, a defect of the program - is on the run's line with its code,
 * the server's message and its time IN THE TEXT, for a run the server ended (`error`) and for a run that goes on; a refusal
 * of a payment (`ROUTING_*`, `CONFLICT`, `INVALID_INPUT`, `PAYMENT_REJECTED`) is not a failure of the run: no line, and a
 * `tx.failed` with it is counted as a rejection (the history shows it as an outcome), while a `tx.failed` with a real
 * failure (`INTEGRITY_VIOLATION`, stream A's new code) is counted as an error.
 * Actual useSimulatorRealMode with the production predicate, REAL SSE transport (api/sse.ts) over a stubbed fetch, the
 * normaliser and the pipeline of the stream; TopBar mounted for the drawn text.
 */
import { computed, createApp, h, nextTick, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getRun } from '../api/simulatorApi'
import TopBar from '../components/TopBar.vue'
import { isUserFacingRunErrorCode } from '../utils/runErrorClassification'
import { provideTopBarContext, type TopBarContext } from './useTopBarContext'
import { useSimulatorRealMode, type RealModeState } from './useSimulatorRealMode'

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

const AT = '2026-10-30T12:34:56Z'
/** One code of each class of a real failure (the owner's list). */
const REAL_FAILURES = [
  ['infrastructure', 'PAYMENT_TIMEOUT'],
  ['infrastructure', 'HEARTBEAT_FAILED'],
  ['integrity', 'INTEGRITY_VIOLATION'],
  ['program', 'INTERNAL_ERROR'],
  ['program', 'SENDER_NOT_FOUND'],
  ['clearing', 'CLEARING_ERROR'],
  ['clearing', 'CLEARING_REFUSED'],
  ['run guard', 'REAL_MODE_TOO_MANY_ERRORS'],
  ['run guard', 'REAL_MODE_MONEY_NO_PROGRESS'],
] as const
const REFUSALS = ['ROUTING_NO_ROUTE', 'ROUTING_NO_CAPACITY', 'CONFLICT', 'INVALID_INPUT', 'PAYMENT_REJECTED'] as const

const statusOf = (state: string) => ({
  run_id: 'r1', scenario_id: 'sc1', state, sim_time_ms: 0, intensity_percent: 30, ops_sec: 0, queue_depth: 0,
  last_event_type: null, current_phase: null, last_error: null,
})

type Conn = { push: (text: string) => void }
function stubSseFetch() {
  const conns: Conn[] = []
  const enc = new TextEncoder()
  vi.stubGlobal('fetch', vi.fn(async (url: unknown) => {
    if (!String(url).includes('/events')) return new Response('{}', { status: 200 })
    let ctl!: ReadableStreamDefaultController<Uint8Array>
    const stream = new ReadableStream<Uint8Array>({ start(c) { ctl = c } })
    conns.push({ push: (text) => ctl.enqueue(enc.encode(text)) })
    return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
  }))
  return conns
}
let n = 0
const frame = (payload: Record<string, unknown>) => {
  const id = `evt_r1_${String(++n).padStart(6, '0')}`
  return `id: ${id}\nevent: simulator.event\ndata: ${JSON.stringify({ event_id: id, ts: AT, ...payload })}\n\n`
}
const statusFrame = (state: string, code: string | null) => frame({
  type: 'run_status', run_id: 'r1', scenario_id: 'sc1', state,
  last_error: code ? { code, message: `${code} message from the server`, at: AT } : null,
})
const txFailedFrame = (code: string) => frame({
  type: 'tx.failed', equivalent: 'UAH', from: 'A', to: 'B', error: { code, message: `${code} message`, at: AT },
})

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
    // The production predicate (`useSimulatorApp.ts` passes the same function).
    isUserFacingRunError: isUserFacingRunErrorCode,
    inc: (map, key) => { map[key] = (map[key] ?? 0) + 1 },
    loadScene: async () => undefined, loadRecoveryScene: async () => true,
    realPatchApplier: { applyNodePatches: () => undefined, applyEdgePatches: () => undefined, applyNodeStatus: () => undefined, applyEdgeStatus: () => undefined },
    pushTxAmountLabel: () => undefined, routeArrivalMs: () => 0, scheduleTimeout: () => undefined,
    runRealTxFx: () => undefined, runRealClearingDoneFx: () => undefined, wakeUp: () => undefined, onAnySseEvent: () => undefined,
  })
  return { h, isRealModeRef, real }
}

async function bootRunning() {
  const conns = stubSseFetch()
  vi.mocked(getRun).mockResolvedValue(statusOf('running') as never)
  const hr = harness()
  hr.isRealModeRef.value = true
  await nextTick(); await vi.advanceTimersByTimeAsync(0)
  await vi.waitFor(() => expect(conns.length).toBe(1))
  conns[0]!.push(statusFrame('running', null)); await vi.advanceTimersByTimeAsync(0)
  expect(hr.real.runStatus?.state, 'stand: boot running').toBe('running')
  return { ...hr, conns }
}

function drawnLine(lastError: string): string {
  const ctx = {
    apiMode: ref('real'), isTestMode: ref(false), uiTheme: ref('saas'),
    loadingScenarios: ref(false), scenarios: ref([]), selectedScenarioId: ref('sc1'), intensityPercent: ref(30), runId: ref('r1'),
    runStatus: ref({ run_id: 'r1', scenario_id: 'sc1', state: 'error' }), sseState: ref('closed'), lastError: ref(lastError),
    runStats: ref({ startedAtMs: 0, attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} }),
    accessToken: ref(null),
  } as unknown as TopBarContext
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ setup() { provideTopBarContext(ctx); return () => h(TopBar) } })
  app.mount(host)
  const text = host.querySelector('[data-testid="error-text"]')?.textContent ?? ''
  app.unmount(); host.remove()
  return text
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.spyOn(Math, 'random').mockReturnValue(0.5)
  vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('054-B-7b: a real failure is on the run line with its code and message; a refusal of a payment is not', () => {
  for (const [klass, code] of REAL_FAILURES) {
    it(`${klass} ${code}, the run ended (error): code, message and time drawn`, async () => {
      const { real, conns } = await bootRunning()
      conns[0]!.push(statusFrame('error', code)); await vi.advanceTimersByTimeAsync(0)
      const drawn = drawnLine(real.lastError)
      expect(drawn, 'the line is drawn').not.toBe('')
      expect(drawn, 'the code and the message are in the text').toContain(`${code} — ${code} message from the server`)
      expect(drawn, 'and the time').toContain(AT)
      expect(drawn, 'not the old «reload the page» sentence').not.toMatch(/Reload the page|Перезагрузите страницу/)
    })

    it(`${klass} ${code}, the run goes on: code, message and time drawn`, async () => {
      const { real, conns } = await bootRunning()
      conns[0]!.push(statusFrame('running', code)); await vi.advanceTimersByTimeAsync(0)
      expect(real.lastError).toContain(`${code} — ${code} message from the server`)
      expect(real.lastError).toContain(AT)
    })
  }

  for (const code of REFUSALS) {
    it(`counter-check: the refusal ${code} on a run that goes on is not a line of the run`, async () => {
      const { real, conns } = await bootRunning()
      conns[0]!.push(statusFrame('running', code)); await vi.advanceTimersByTimeAsync(0)
      expect(real.runStatus?.last_error?.code, 'stand: the status carried the code').toBe(code)
      expect(real.lastError).toBe('')
    })
  }

  it('tx.failed: INTEGRITY_VIOLATION is counted as an error; ROUTING_NO_ROUTE as a rejection; neither writes the run line', async () => {
    const { real, conns } = await bootRunning()
    conns[0]!.push(txFailedFrame('INTEGRITY_VIOLATION'))
    conns[0]!.push(txFailedFrame('ROUTING_NO_ROUTE'))
    await vi.advanceTimersByTimeAsync(0)
    expect(real.runStats.errorsByCode).toEqual({ INTEGRITY_VIOLATION: 1 })
    expect(real.runStats.rejectedByCode).toEqual({ ROUTING_NO_ROUTE: 1 })
    expect(real.lastError, 'a payment outcome is not the run line').toBe('')
  })
})
