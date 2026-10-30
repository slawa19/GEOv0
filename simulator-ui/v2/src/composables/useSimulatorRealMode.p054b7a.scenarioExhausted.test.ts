/**
 * 054-B-7a (the owner's decision of 2026-10-30): a run the server paused because its scenario's automatic payments are used
 * up (`pause_reason: "scenario_exhausted"`, the contract of stream A) says so on the run's line - the same line, the same
 * place as the run's other status lines (`runStatusErrorLine` -> `lastError` -> the top bar's error line); "Поток" (resume)
 * stays. The capacity-stall alert it replaces is gone (no alert at any `consec_all_rejected_ticks`).
 * Counter-checks: a paused run without a reason, or with a reason the UI does not know, has no line; a status without the
 * field (an older backend) is read without a failure, by the REST decoder and by the stream.
 * Actual useSimulatorRealMode, REAL SSE transport (api/sse.ts) over a stubbed fetch; the REST decoder itself; TopBar mounted.
 */
import { computed, createApp, h, nextTick, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getRun } from '../api/simulatorApi'
import { decodeRunStatusResponse } from '../api/simulatorContracts'
import type { RunStatus } from '../api/simulatorTypes'
import { t } from '../i18n/t'
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
  setIntensity: vi.fn(async () => undefined),
  stopRun: vi.fn(async () => undefined),
}))
vi.mock('../api/equivalentsApi', () => ({ fetchEquivalentPrecisions: vi.fn(async () => []) }))

const statusOf = (state: string, extra: Record<string, unknown> = {}) => ({
  run_id: 'r1', scenario_id: 'sc1', state, sim_time_ms: 0, intensity_percent: 30, ops_sec: 0, queue_depth: 0,
  last_event_type: null, current_phase: null, last_error: null, ...extra,
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

function mountTopBar(runStatus: RunStatus, lastError: string) {
  const ctx = {
    apiMode: ref('real'), isTestMode: ref(false), uiTheme: ref('saas'),
    loadingScenarios: ref(false), scenarios: ref([{ scenario_id: 'sc1', label: 'One' }]),
    selectedScenarioId: ref('sc1'), intensityPercent: ref(30), runId: ref('r1'), runStatus: ref(runStatus), sseState: ref('open'),
    lastError: ref(lastError),
    runStats: ref({ startedAtMs: 0, attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} }),
    accessToken: ref(null),
  } as unknown as TopBarContext
  const host = document.createElement('div')
  document.body.appendChild(host)
  const onResume = vi.fn()
  const app = createApp({ setup() { provideTopBarContext(ctx); return () => h(TopBar, { onResume }) } })
  app.mount(host)
  return { host, onResume, done: () => { app.unmount(); host.remove() } }
}

beforeEach(() => { vi.useFakeTimers(); vi.spyOn(Math, 'random').mockReturnValue(0.5) })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); document.body.innerHTML = '' })

describe('054-B-7a: a run whose scenario payments are used up says so; no stall alert', () => {
  it('the stream says paused with `scenario_exhausted`: the line is on screen; "Поток" resumes', async () => {
    const { real, conns } = await bootRunning()
    conns[0]!.push(statusFrame('paused', { pause_reason: 'scenario_exhausted' })); await vi.advanceTimersByTimeAsync(0)
    expect(real.runStatus?.state).toBe('paused')
    expect(real.runStatus?.pause_reason, 'the stream keeps the field').toBe('scenario_exhausted')
    expect(real.lastError, 'the line of the used-up scenario').toBe(t('runLine.scenarioExhausted'))

    const { host, onResume, done } = mountTopBar(real.runStatus!, real.lastError)
    await nextTick()
    expect(host.querySelector('[data-testid="error-text"]')?.textContent, 'on the run\'s line').toBe(t('runLine.scenarioExhausted'))
    host.querySelector<HTMLButtonElement>('[data-testid="flow-toggle"]')!.click()
    expect(onResume, '"Поток" resumes the run').toHaveBeenCalledTimes(1)
    done()

    // The run resumed: the line goes with the reason.
    conns[0]!.push(statusFrame('running')); await vi.advanceTimersByTimeAsync(0)
    expect(real.lastError).toBe('')
  })

  it('the REST read of the status keeps the field and writes the same line', async () => {
    const decoded = decodeRunStatusResponse({
      api_version: 'simulator-api/1', mode: 'real', ...statusOf('paused', { pause_reason: 'scenario_exhausted' }),
    })
    expect(decoded.pause_reason).toBe('scenario_exhausted')

    const { h, real } = await bootRunning()
    vi.mocked(getRun).mockResolvedValue(decoded as never)
    await h.refreshRunStatus()
    expect(real.lastError).toBe(t('runLine.scenarioExhausted'))
  })

  for (const [what, extra] of [
    ['no reason', {}],
    ['a null reason', { pause_reason: null }],
    ['a reason the UI does not know', { pause_reason: 'something_else' }],
  ] as const) {
    it(`counter-check: a paused run with ${what} has no line`, async () => {
      const { real, conns } = await bootRunning()
      conns[0]!.push(statusFrame('paused', extra)); await vi.advanceTimersByTimeAsync(0)
      expect(real.runStatus?.state, 'stand: the status was applied').toBe('paused')
      expect(real.lastError).toBe('')
    })
  }

  it('counter-check: a status without the field (an older backend) is decoded without a failure', () => {
    const decoded = decodeRunStatusResponse({ api_version: 'simulator-api/1', mode: 'real', ...statusOf('paused') })
    expect(decoded.state).toBe('paused')
    expect(decoded.pause_reason ?? null).toBeNull()
  })

  it('no stall alert at any count of all-rejected ticks', async () => {
    for (const ticks of [0, 3, 50]) {
      const { host, done } = mountTopBar({ run_id: 'r1', scenario_id: 'sc1', state: 'running', consec_all_rejected_ticks: ticks } as RunStatus, '')
      await nextTick()
      expect(host.querySelector('[data-testid="network-state"]'), 'stand: the bar is mounted').not.toBeNull()
      expect(host.querySelector('[data-testid="capacity-stall"]'), `${ticks} ticks`).toBeNull()
      done()
    }
  })
})
