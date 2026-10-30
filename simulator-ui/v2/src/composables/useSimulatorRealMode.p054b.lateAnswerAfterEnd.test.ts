/**
 * 054-B-4 fix-delta (§15 Codex R2, P2): a late answer of a control command does not undo an ENDED status of its run.
 * Before: `control.latest()` checked the command's seq and run only - a Resume answered `running` after the stream said
 * `error`, the stream closed and the status read confirmed `error`, put `running` back: "Поток" returned and "Перезапустить"
 * went away over a run the server had ended, with its stream closed (`sseState: closed`).
 * Counter-check: a late answer over a status that is not ended is applied as before (the latest command's answer wins).
 * Actual useSimulatorRealMode, REAL SSE transport (api/sse.ts) over a stubbed fetch; TopBar mounted for the controls.
 */
import { computed, createApp, h, nextTick, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getRun, resumeRun, setIntensity } from '../api/simulatorApi'
import type { RunStatus } from '../api/simulatorTypes'
import TopBar from '../components/TopBar.vue'
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
  setIntensity: vi.fn(),
  stopRun: vi.fn(async () => undefined),
}))
vi.mock('../api/equivalentsApi', () => ({ fetchEquivalentPrecisions: vi.fn(async () => []) }))

const LAST_ERROR = { code: 'REAL_MODE_TOO_MANY_ERRORS', message: 'too many errors', at: '2026-10-30T00:00:00Z' }
const statusOf = (state: string, extra: Record<string, unknown> = {}) => ({
  run_id: 'r1', scenario_id: 'sc1', state, sim_time_ms: 0, intensity_percent: 30, ops_sec: 0, queue_depth: 0,
  last_event_type: null, current_phase: null, last_error: null, ...extra,
})

type Conn = { push: (text: string) => void; close: () => void }
function stubSseFetch() {
  const conns: Conn[] = []
  const enc = new TextEncoder()
  vi.stubGlobal('fetch', vi.fn(async (url: unknown) => {
    if (!String(url).includes('/events')) return new Response('{}', { status: 200 })
    let ctl!: ReadableStreamDefaultController<Uint8Array>
    const stream = new ReadableStream<Uint8Array>({ start(c) { ctl = c } })
    conns.push({ push: (t) => ctl.enqueue(enc.encode(t)), close: () => ctl.close() })
    return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
  }))
  return conns
}
let n = 0
const statusFrame = (state: string, extra: Record<string, unknown> = {}) => {
  const id = `evt_r1_${String(++n).padStart(6, '0')}`
  return `id: ${id}\nevent: simulator.event\ndata: ${JSON.stringify({ event_id: id, ts: '2026-10-30T00:00:00Z', type: 'run_status', run_id: 'r1', scenario_id: 'sc1', state, last_error: null, ...extra })}\n\n`
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

async function boot(state: string) {
  const conns = stubSseFetch()
  vi.mocked(getRun).mockResolvedValue(statusOf(state) as never)
  const hr = harness()
  hr.isRealModeRef.value = true
  await nextTick(); await vi.advanceTimersByTimeAsync(0)
  await vi.waitFor(() => expect(conns.length).toBe(1))
  conns[0]!.push(statusFrame(state)); await vi.advanceTimersByTimeAsync(0)
  expect(hr.real.runStatus?.state, `stand: boot ${state}`).toBe(state)
  return { ...hr, conns }
}

function controls(runStatus: RunStatus | null) {
  const ctx = {
    apiMode: ref('real'), isTestMode: ref(false), uiTheme: ref('saas'),
    loadingScenarios: ref(false), scenarios: ref([]), selectedScenarioId: ref('sc1'), intensityPercent: ref(30), runId: ref('r1'),
    runStatus: ref(runStatus), sseState: ref('closed'), lastError: ref(''),
    runStats: ref({ startedAtMs: 0, attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} }),
    accessToken: ref(null),
  } as unknown as TopBarContext
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ setup() { provideTopBarContext(ctx); return () => h(TopBar) } })
  app.mount(host)
  const out = {
    flow: host.querySelector('[data-testid="flow-toggle"]') !== null,
    restart: host.querySelector('[data-testid="run-restart"]') !== null,
  }
  app.unmount(); host.remove()
  return out
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.spyOn(Math, 'random').mockReturnValue(0.5)
  vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('054-B-4 fix-delta: a late command answer does not undo an ended run status', () => {
  for (const command of ['resume', 'tempo'] as const) {
    it(`a ${command} answered "running" after the stream and the status read said "error": the run stays ended`, async () => {
      const { h, real, conns } = await boot(command === 'resume' ? 'paused' : 'running')
      let release!: () => void
      const held = () => new Promise((res) => { release = () => res(statusOf('running')) })
      if (command === 'resume') vi.mocked(resumeRun).mockImplementation(held as never)
      else vi.mocked(setIntensity).mockImplementation(held as never)
      const answer = command === 'resume' ? h.resume() : h.applyIntensity()

      // The server ends the run: the stream says so, then closes; the status read confirms it.
      vi.mocked(getRun).mockResolvedValue(statusOf('error', { last_error: LAST_ERROR }) as never)
      conns[0]!.push(statusFrame('error', { last_error: LAST_ERROR }))
      await vi.advanceTimersByTimeAsync(0)
      conns[0]!.close()
      await vi.advanceTimersByTimeAsync(0)
      await vi.waitFor(() => expect(real.sseState, 'stand: the stream of the ended run is closed').toBe('closed'))
      expect(real.runStatus?.state, 'stand: ended').toBe('error')

      release(); await answer
      expect(real.runStatus?.state, 'the late answer does not bring the run back').toBe('error')
      expect(controls(real.runStatus), 'the controls of an ended run').toEqual({ flow: false, restart: true })
    })
  }

  it('counter-check: a late resume answer over a status that is not ended is applied as before', async () => {
    const { h, real, conns } = await boot('paused')
    let release!: () => void
    vi.mocked(resumeRun).mockImplementation((() => new Promise((res) => { release = () => res(statusOf('running', { sim_time_ms: 777 })) })) as never)
    const answer = h.resume()
    conns[0]!.push(statusFrame('paused')); await vi.advanceTimersByTimeAsync(0)
    release(); await answer
    expect(real.runStatus?.state).toBe('running')
    expect(real.runStatus?.sim_time_ms).toBe(777)
    expect(controls(real.runStatus)).toEqual({ flow: true, restart: false })
  })
})
