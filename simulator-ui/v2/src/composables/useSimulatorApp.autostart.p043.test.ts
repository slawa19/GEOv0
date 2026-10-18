/**
 * 043 S2 - the opening of the one screen and the pause on a manual action, on the real composable (decisions 2 and 3).
 * The API functions are mocked at the module boundary; everything between them and the state is the shipped code.
 *
 * The walk (AGENTS.md section 16): the first open with no run, with a running run, with a paused one, with an ended one
 * (stopped, error), with a run whose status cannot be read; the stored intensity (55, a stale 0); the refusals of the
 * create (409 with an active run; a failure) and an empty catalogue; the offline source; one attempt per load; a manual
 * action (a protocol flow, a chosen participant) on a running run, the way back (only `resume`), an action taken while
 * the opening's run is still being created, and the offline source where nothing is paused.
 */
import { effectScope, nextTick, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/http'
import { connectSse } from '../api/sse'
import {
  createRun, getActiveRun, getRun, getScenarioPreview, getSnapshot, listScenarios, pauseRun, resumeRun, stopRun,
} from '../api/simulatorApi'
import type { SimulatorGraphSnapshot } from '../api/simulatorTypes'
import { keyEdge } from '../utils/edgeKey'
import { autostartDecision } from './useInteractAutoBootstrapRun'
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
    createRun: vi.fn(),
    pauseRun: vi.fn(),
    resumeRun: vi.fn(),
    stopRun: vi.fn(),
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

function snap(nodeId: string): SimulatorGraphSnapshot {
  return {
    equivalent: 'UAH',
    generated_at: '2026-10-16T00:00:00Z',
    nodes: [{ id: nodeId, name: nodeId, type: 'person', status: 'active' }, { id: 'B', name: 'B', type: 'person', status: 'active' }],
    // One line between the two nodes (the node of the run is named after it): "Inspect edge" and a line click need it.
    links: [{ source: nodeId, target: 'B', trust_limit: '100', used: '0', available: '100', status: 'active' }],
  } as unknown as SimulatorGraphSnapshot
}

/** The backend's runs, by id: their state follows create / pause / resume / stop, as the server's does. */
const runs = new Map<string, string>()
const status = (runId: string) => ({
  run_id: runId, scenario_id: 'sc1', state: runs.get(runId) ?? 'running', api_version: 'simulator-api/1', mode: 'real',
  sim_time_ms: 0, intensity_percent: 30, ops_sec: 0, queue_depth: 0, last_event_type: null, current_phase: null, last_error: null,
})

async function settle(): Promise<void> {
  for (let round = 0; round < 3; round += 1) {
    for (let i = 0; i < 20; i += 1) {
      await Promise.resolve()
      await nextTick()
    }
    await vi.advanceTimersByTimeAsync(200)
  }
}

let scope: EffectScope | null = null
function boot() {
  scope = effectScope()
  return scope.run(() => useSimulatorApp())!
}
/** A returning visitor: the run id the browser kept, whose state the backend reports. */
function storedRun(runId: string, state: string) {
  window.localStorage.setItem('geo.sim.v2.runId', runId)
  runs.set(runId, state)
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
  vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  runs.clear()
  for (const m of [listScenarios, getActiveRun, getRun, getSnapshot, getScenarioPreview, createRun, pauseRun, resumeRun, stopRun]) vi.mocked(m).mockReset()
  vi.mocked(connectSse).mockClear()
  vi.mocked(listScenarios).mockResolvedValue({ api_version: 'simulator-api/1', items: [{ scenario_id: 'sc1', label: 'Scenario 1' }] } as never)
  vi.mocked(getActiveRun).mockResolvedValue({ run_id: null } as never)
  vi.mocked(getRun).mockImplementation((async (_c: unknown, id: string) => status(id)) as never)
  vi.mocked(getSnapshot).mockImplementation((async (_c: unknown, id: string) => snap(`${id}_NODE`)) as never)
  vi.mocked(getScenarioPreview).mockImplementation((async () => snap('PREVIEW_NODE')) as never)
  vi.mocked(createRun).mockImplementation((async () => { runs.set('R_NEW', 'running'); return { run_id: 'R_NEW' } }) as never)
  vi.mocked(pauseRun).mockImplementation((async (_c: unknown, id: string) => { runs.set(id, 'paused'); return status(id) }) as never)
  vi.mocked(resumeRun).mockImplementation((async (_c: unknown, id: string) => { runs.set(id, 'running'); return status(id) }) as never)
  vi.mocked(stopRun).mockImplementation((async (_c: unknown, id: string) => { runs.set(id, 'stopped'); return status(id) }) as never)
  window.history.replaceState({}, '', '/')
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  scope?.stop()
  scope = null
  window.history.replaceState({}, '', '/')
  window.localStorage.clear()
})

describe('043 S2 (decision 2): opening the demo is a live run', () => {
  it('a first visit to `/` (no ?mode, nothing stored): the server source, ONE run created at intensity 30, not paused', async () => {
    const app = boot()
    await settle()
    expect(app.apiMode.value, 'the default source is the server').toBe('real')
    expect(app.isLiveUi.value).toBe(true)
    expect(createRun).toHaveBeenCalledTimes(1)
    expect(vi.mocked(createRun).mock.calls[0]![1]).toMatchObject({ scenario_id: 'sc1', mode: 'real', intensity_percent: 30 })
    expect(pauseRun).not.toHaveBeenCalled()
    expect([app.real.runId, app.real.runStatus?.state]).toEqual(['R_NEW', 'running'])
  })

  it('a running run is attached and left alone: no create, no pause, no resume', async () => {
    storedRun('R1', 'running')
    const app = boot()
    await settle()
    expect([createRun, pauseRun, resumeRun].map((f) => vi.mocked(f).mock.calls.length)).toEqual([0, 0, 0])
    expect(app.real.runStatus?.state).toBe('running')
  })

  it('a run somebody paused stays paused: opening is not a resume, and no second run is put next to it', async () => {
    storedRun('R1', 'paused')
    const app = boot()
    await settle()
    expect([createRun, resumeRun].map((f) => vi.mocked(f).mock.calls.length)).toEqual([0, 0])
    expect(app.real.runStatus?.state).toBe('paused')
  })

  it('an active run of this visitor found by the backend (another tab) is attached, not doubled', async () => {
    vi.mocked(getActiveRun).mockResolvedValue({ run_id: 'R_TAB' } as never)
    runs.set('R_TAB', 'paused')
    const app = boot()
    await settle()
    expect(createRun).not.toHaveBeenCalled()
    expect(app.real.runId).toBe('R_TAB')
  })

  for (const ended of ['stopped', 'error']) {
    it(`an ended run (${ended}) is replaced by a live one`, async () => {
      storedRun('R_OLD', ended)
      const app = boot()
      await settle()
      expect(createRun).toHaveBeenCalledTimes(1)
      expect(app.real.runId).toBe('R_NEW')
    })
  }

  it('a run whose status cannot be read (HTTP 500): nothing is started next to a run that may be live', async () => {
    storedRun('R1', 'running')
    vi.mocked(getRun).mockRejectedValue(new ApiError('HTTP 500 for /simulator/runs/R1', { status: 500 }))
    const app = boot()
    await settle()
    expect(createRun).not.toHaveBeenCalled()
    expect(app.real.lastError, 'the failed read is on screen').toContain('500')
  })

  it('the stored intensity is used (55); a stale stored 0 (old Interact) becomes 30', async () => {
    window.localStorage.setItem('geo.sim.v2.intensityPercent', '55')
    boot()
    await settle()
    expect(vi.mocked(createRun).mock.calls[0]![1]).toMatchObject({ intensity_percent: 55 })
    scope?.stop()
    vi.mocked(createRun).mockClear()
    window.localStorage.removeItem('geo.sim.v2.runId') // a fresh visit again, only the stale 0 stored
    window.localStorage.setItem('geo.sim.v2.intensityPercent', '0')
    boot()
    await settle()
    expect(vi.mocked(createRun).mock.calls[0]![1]).toMatchObject({ intensity_percent: 30 })
    // 043 S3: the stale 0 is cleaned from storage at load (it no longer waits for a reset after the wiring).
    expect(window.localStorage.getItem('geo.sim.v2.intensityPercent'), 'the stale 0 is gone from storage').toBeNull()
  })

  it('409 (the backend already runs one for this visitor): the active run is attached', async () => {
    vi.mocked(getActiveRun).mockResolvedValueOnce({ run_id: null } as never).mockResolvedValue({ run_id: 'R_ACTIVE' } as never)
    vi.mocked(createRun).mockRejectedValue(new ApiError('HTTP 409 Conflict for /simulator/runs', { status: 409 }))
    runs.set('R_ACTIVE', 'running')
    const app = boot()
    await settle()
    expect(createRun).toHaveBeenCalledTimes(1)
    expect(app.real.runId).toBe('R_ACTIVE')
  })

  it('a refused create (no backend run possible): the error is on screen, ONE attempt per load, Start remains', async () => {
    vi.mocked(createRun).mockRejectedValue(new ApiError('HTTP 503 for /simulator/runs', { status: 503 }))
    const app = boot()
    await settle()
    await vi.advanceTimersByTimeAsync(10_000)
    await settle()
    expect(createRun, 'not retried behind the person').toHaveBeenCalledTimes(1)
    expect(app.real.lastError).toContain('503')
    expect(app.real.runId).toBeNull()
  })

  // 043 S2 follow-up: the 409 of the global limit of active runs (a second visitor on a shared server) gets a human text.
  const conflict = (kind: string) => new ApiError('HTTP 409 Conflict for /simulator/runs', {
    status: 409,
    bodyText: JSON.stringify({ error: { code: 'E008', message: 'Global active runs limit reached', details: { conflict_kind: kind, max_active_runs: 1, active_runs: 1 } } }),
  })

  it('a create refused by the global limit of active runs (409 global_active_limit): the error line is a human text, in the interface language', async () => {
    vi.mocked(createRun).mockRejectedValue(conflict('global_active_limit'))
    document.documentElement.lang = 'ru'
    try {
      const app = boot()
      await settle()
      expect(app.real.runId).toBeNull()
      // 044 stage 2: the text names the number of places from `details.max_active_runs` (1 here).
      expect(app.real.lastError).toBe('Сейчас занято единственное место: демо смотрит 1 человек. Попробуйте через минуту — Start в «Подробнее».')
    } finally {
      document.documentElement.lang = ''
    }
    scope?.stop()
    const en = boot()
    await settle()
    expect(en.real.lastError).toBe('The only place is taken right now: 1 person is watching the demo. Try again in a minute - Start under "Подробнее".')
  })

  it('counter-check: a 409 of another kind (owner_active_exists) and a non-409 error keep their old text', async () => {
    vi.mocked(createRun).mockRejectedValue(conflict('owner_active_exists'))
    const app = boot()
    await settle()
    expect(app.real.lastError, 'another 409: the raw message as before').toBe('HTTP 409 Conflict for /simulator/runs')
    scope?.stop()
    window.localStorage.clear()
    vi.mocked(createRun).mockRejectedValue(new ApiError('HTTP 500 for /simulator/runs', { status: 500, bodyText: JSON.stringify({ error: { details: { conflict_kind: 'global_active_limit' } } }) }))
    const other = boot()
    await settle()
    expect(other.real.lastError, 'a non-409 error, even with that kind in its body: as before').toBe('HTTP 500 for /simulator/runs')
  })

  it('an empty catalogue: no scenario, nothing started', async () => {
    vi.mocked(listScenarios).mockResolvedValue({ api_version: 'simulator-api/1', items: [] } as never)
    boot()
    await settle()
    expect(createRun).not.toHaveBeenCalled()
  })

  it('the offline source (?mode=fixtures): no backend call, nothing started', async () => {
    window.history.replaceState({}, '', '/?mode=fixtures')
    const app = boot()
    await settle()
    expect(app.isLiveUi.value).toBe(false)
    expect([listScenarios, createRun].map((f) => vi.mocked(f).mock.calls.length)).toEqual([0, 0])
  })

  it('a run the person stops is not restarted behind them (one attempt per load)', async () => {
    const app = boot()
    await settle()
    expect(createRun).toHaveBeenCalledTimes(1)
    await app.realActions.stop()
    await settle()
    await vi.advanceTimersByTimeAsync(10_000)
    await settle()
    expect(createRun).toHaveBeenCalledTimes(1)
    expect(app.real.runStatus?.state).toBe('stopped')
  })

  it('the decision itself, by state (anti-vacuum of the rule: each branch is reached)', () => {
    const st = (state: string) => status('R') as unknown as Parameters<typeof autostartDecision>[1] & { state: string }
    expect(autostartDecision(null, null)).toBe('start')
    expect(autostartDecision('R', null)).toBe('unknown')
    for (const s of ['running', 'paused', 'created', 'stopping']) expect(autostartDecision('R', { ...st(s), state: s } as never)).toBe('attached')
    for (const s of ['stopped', 'error']) expect(autostartDecision('R', { ...st(s), state: s } as never)).toBe('start')
  })
})

describe('043 S2 (decision 3): a manual action pauses the automatic payments; only "Поток" brings them back', () => {
  it('a protocol flow on a running run: one pause; cancel does not resume; resume does; a new flow pauses again', async () => {
    storedRun('R1', 'running')
    const app = boot()
    await settle()
    app.interact.mode.startPaymentFlow()
    await settle()
    expect(pauseRun).toHaveBeenCalledTimes(1)
    expect(app.real.runStatus?.state).toBe('paused')

    app.interact.mode.cancel()
    await settle()
    expect(resumeRun, 'cancel does not resume').not.toHaveBeenCalled()
    expect(app.real.runStatus?.state).toBe('paused')

    await app.realActions.resume()
    await settle()
    expect(app.real.runStatus?.state).toBe('running')

    app.interact.mode.startClearingFlow()
    await settle()
    expect(pauseRun, 'the next action pauses again').toHaveBeenCalledTimes(2)
  })

  it('a resume while the panel is open is honoured: the open panel does not pause it again', async () => {
    storedRun('R1', 'running')
    const app = boot()
    await settle()
    app.interact.mode.startTrustlineFlow()
    await settle()
    await app.realActions.resume()
    await settle()
    expect(pauseRun).toHaveBeenCalledTimes(1)
    expect(app.real.runStatus?.state).toBe('running')
  })

  // 043 S2 fix-delta 2 (2026-10-17) REVERSES what this test held: a selection set in STATE (as loading sets it - the
  // `?focus=` deep link) is not a manual act. The explicit canvas click is held in the browser,
  // `e2e/qa/one-screen.qa.ts` "fix-delta 1" (the same participant clicked again) and "(c) choosing a participant".
  it('a selection set in state (as loading sets it) pauses nothing; an explicit entry does', async () => {
    storedRun('R1', 'running')
    const app = boot()
    await settle()
    app.state.selectedNodeId = 'R1_NODE'
    await settle()
    expect(pauseRun, 'a selection by state is not an act').not.toHaveBeenCalled()
    app.interact.mode.selectNode('B')
    await settle()
    expect(pauseRun, 'an explicit pick is').toHaveBeenCalledTimes(1)
  })

  it('a paused run is not paused twice (no request on a run that is already quiet)', async () => {
    storedRun('R1', 'paused')
    const app = boot()
    await settle()
    app.interact.mode.startPaymentFlow()
    await settle()
    expect(pauseRun).not.toHaveBeenCalled()
  })

  it('an action taken while the opening\'s run is still being created: the run is paused the moment it runs', async () => {
    let release: (() => void) | null = null
    vi.mocked(createRun).mockImplementation((() => new Promise((ok) => {
      release = () => { runs.set('R_NEW', 'running'); ok({ run_id: 'R_NEW' }) }
    })) as never)
    const app = boot()
    await settle()
    expect(createRun, 'precondition: the create is in flight').toHaveBeenCalledTimes(1)
    app.interact.mode.startPaymentFlow()
    await settle()
    expect(pauseRun, 'nothing to pause yet').not.toHaveBeenCalled()
    release!()
    await settle()
    expect(pauseRun, 'paused once it runs').toHaveBeenCalledTimes(1)
    expect(app.real.runStatus?.state).toBe('paused')
  })

  it('an action before the opening is over: the run is created already paused', async () => {
    let releasePreview: (() => void) | null = null
    vi.mocked(getScenarioPreview).mockImplementation((() => new Promise((ok) => { releasePreview = () => ok(snap('PREVIEW_NODE')) })) as never)
    const app = boot()
    await settle()
    app.interact.mode.startPaymentFlow()
    await settle()
    releasePreview!()
    await settle()
    expect(createRun).toHaveBeenCalledTimes(1)
    expect(pauseRun, 'started paused (pauseImmediately)').toHaveBeenCalledTimes(1)
    expect(app.real.runStatus?.state).toBe('paused')
  })

  it('the offline source: choosing a participant pauses nothing (there is no run)', async () => {
    window.history.replaceState({}, '', '/?mode=fixtures')
    const app = boot()
    await settle()
    app.state.selectedNodeId = 'B'
    await settle()
    expect(pauseRun).not.toHaveBeenCalled()
  })
})

describe('043 S2 fix round (external review 2026-10-17): one create per start, a pause on every explicit entry', () => {
  /** A create held open until the test lets it go: the window in which a second start could slip in. */
  function heldCreate(): { release: () => void } {
    const h = { release: () => {} }
    vi.mocked(createRun).mockImplementation((() => new Promise((ok) => {
      h.release = () => { runs.set('R_NEW', 'running'); ok({ run_id: 'R_NEW' }) }
    })) as never)
    return h
  }

  it('P2-1: two concurrent starts (Start pressed while the opening\'s start is in flight) issue exactly ONE create', async () => {
    storedRun('R1', 'stopped')
    window.localStorage.setItem('geo.sim.v2.runId', '')
    runs.clear()
    const held = heldCreate()
    const app = boot()
    await settle()
    expect(createRun, 'precondition: the opening\'s create is in flight').toHaveBeenCalledTimes(1)
    const second = app.realActions.startRun()
    await settle()
    held.release()
    await second
    await settle()
    expect(createRun, 'the second start joined the first').toHaveBeenCalledTimes(1)
    expect(app.real.runId).toBe('R_NEW')
  })

  it('P2-1: `?autostart=1` (the old dev-only autostart) no longer starts a second run next to the opening\'s', async () => {
    window.history.replaceState({}, '', '/?autostart=1')
    const held = heldCreate()
    boot()
    await settle()
    held.release()
    await settle()
    expect(createRun).toHaveBeenCalledTimes(1)
  })

  for (const ended of ['stopped', 'error']) {
    it(`C6: a stored run that has ended (${ended}) gets no live event stream before the opening decides`, async () => {
      storedRun('R_OLD', ended)
      heldCreate() // the opening's create stays in flight: what was opened before it is what the test reads
      boot()
      await settle()
      expect(createRun, 'precondition: the opening is starting a new run').toHaveBeenCalledTimes(1)
      const urls = vi.mocked(connectSse).mock.calls.map((c) => String((c[0] as { url?: string }).url ?? ''))
      expect(urls.filter((u) => u.includes('R_OLD')), 'no stream of the ended run').toEqual([])
    })
  }

  // The race above is won by the opening in this harness (its start resets the ended run before the boot's stream
  // connects), so the stream of an ended run is shown where the opening starts nothing: an empty scenario catalogue
  // (B5). Until 043 S3 this used the Demo UI page, which had no opening; that page is gone.
  it('C6: the boot opens no event stream for an ended stored run (shown where the opening starts nothing: empty catalogue)', async () => {
    vi.mocked(listScenarios).mockResolvedValue({ api_version: 'simulator-api/1', items: [] } as never)
    storedRun('R_OLD', 'stopped')
    const app = boot()
    await settle()
    expect(createRun, 'precondition: the opening starts nothing on this page').not.toHaveBeenCalled()
    expect(vi.mocked(getRun).mock.calls.some((c) => c[1] === 'R_OLD'), 'precondition: the stored run was read').toBe(true)
    const urls = vi.mocked(connectSse).mock.calls.map((c) => String((c[0] as { url?: string }).url ?? ''))
    expect(urls.filter((u) => u.includes('R_OLD')), 'no stream of the ended run').toEqual([])
    expect(app.real.runStatus?.state, 'its status is still read and shown').toBe('stopped')
  })

  it('C6 counter-check: a stored LIVE run (paused) is attached with its stream', async () => {
    storedRun('R1', 'paused')
    boot()
    await settle()
    const urls = vi.mocked(connectSse).mock.calls.map((c) => String((c[0] as { url?: string }).url ?? ''))
    expect(urls.some((u) => u.includes('R1')), 'the stream of the live run is open').toBe(true)
  })

  it('B5: an EMPTY catalogue ends the loading, says so on the error line, starts nothing', async () => {
    vi.mocked(listScenarios).mockResolvedValue({ api_version: 'simulator-api/1', items: [] } as never)
    document.documentElement.lang = 'ru'
    try {
      const app = boot()
      await settle()
      expect(app.state.loading, 'the opening is over').toBe(false)
      expect(app.real.lastError).toBe('На сервере нет сценариев: показать нечего.')
      expect(createRun).not.toHaveBeenCalled()
    } finally {
      document.documentElement.lang = ''
    }
  })

  it('B5: a FAILED catalogue read ends the loading and keeps its error on the error line, starts nothing', async () => {
    vi.mocked(listScenarios).mockRejectedValue(new ApiError('HTTP 503 Service Unavailable for /simulator/scenarios', { status: 503 }))
    const app = boot()
    await settle()
    expect(app.state.loading, 'the opening is over').toBe(false)
    expect(app.real.lastError).toContain('HTTP 503')
    expect(createRun).not.toHaveBeenCalled()
  })

  it('C5: an action during the create of the opening, then a failed first status read (502): the pause still comes when the run reads running', async () => {
    const held = heldCreate()
    const app = boot()
    await settle()
    app.interact.mode.startPaymentFlow() // the person acts while the create is in flight
    await settle()
    vi.mocked(getRun).mockRejectedValueOnce(new ApiError('HTTP 502 Bad Gateway for /simulator/runs/R_NEW', { status: 502 }))
    held.release()
    await settle()
    expect(app.real.runId, 'the run exists').toBe('R_NEW')
    expect(pauseRun, 'nothing to pause yet: its status is unknown').not.toHaveBeenCalled()
    await app.admin.attachRun('R_NEW') // a later read of the status (the stream's run_status does the same)
    await settle()
    expect(pauseRun, 'paused the moment it reads running').toHaveBeenCalledTimes(1)
  })

  /** A running run, a flow already open and the flow brought back by "Поток": the state every entry below starts from. */
  async function openFlowResumed(open: (app: ReturnType<typeof useSimulatorApp>) => void) {
    storedRun('R1', 'running')
    const app = boot()
    await settle()
    open(app)
    await settle()
    expect(pauseRun, 'precondition: opening the flow paused').toHaveBeenCalledTimes(1)
    await app.realActions.resume()
    await settle()
    expect(app.real.runStatus?.state, 'precondition: the flow is back').toBe('running')
    return app
  }

  const ENTRIES: Array<[string, (app: ReturnType<typeof useSimulatorApp>) => void, (app: ReturnType<typeof useSimulatorApp>) => void]> = [
    ['(a) edge popup -> Send Payment: one tick cancels the line flow and starts a payment with both ends',
      (a) => a.interact.mode.startTrustlineFlow(),
      (a) => { a.interact.mode.cancel(); a.interact.mode.startPaymentFlowWithFrom('B'); a.interact.mode.setPaymentToPid('R1_NODE') }],
    ['(b) payment panel: another sender chosen in the dropdown', (a) => a.interact.mode.startPaymentFlow(), (a) => a.interact.mode.setPaymentFromPid('B')],
    ['(b) payment panel: a recipient chosen in the dropdown', (a) => a.interact.mode.startPaymentFlowWithFrom('B'), (a) => a.interact.mode.setPaymentToPid('R1_NODE')],
    ['(b) trust line panel: a creditor chosen in the dropdown', (a) => a.interact.mode.startTrustlineFlow(), (a) => a.interact.mode.setTrustlineFromPid('B')],
    ['(b) trust line panel: a debtor chosen in the dropdown', (a) => a.interact.mode.startTrustlineFlowWithFrom('B'), (a) => a.interact.mode.setTrustlineToPid('R1_NODE')],
    ['(b) trust line panel: a line chosen in the list', (a) => a.interact.mode.startTrustlineFlow(), (a) => a.interact.mode.selectTrustline('B', 'R1_NODE')],
    ['(c) edge popup -> Change Limit (the full editor; the phase does not change)', (a) => a.interact.mode.startTrustlineFlow(), (a) => a.realActions.pauseForManualAction()],
    ['ActionBar: Send Payment pressed again while a payment is being picked', (a) => a.interact.mode.startPaymentFlow(), (a) => { a.interact.mode.cancel(); a.interact.mode.startPaymentFlow() }],
    ['ActionBar: Manage Trustline pressed again', (a) => a.interact.mode.startTrustlineFlow(), (a) => { a.interact.mode.cancel(); a.interact.mode.startTrustlineFlow() }],
    ['ActionBar: Run Clearing pressed again', (a) => a.interact.mode.startClearingFlow(), (a) => { a.interact.mode.cancel(); a.interact.mode.startClearingFlow() }],
    ['node card: Send Payment from a participant, over an open payment', (a) => a.interact.mode.startPaymentFlow(), (a) => { a.interact.mode.cancel(); a.interact.mode.startPaymentFlowWithFrom('B') }],
    ['node card: New trust line from a participant, over an open trust line flow', (a) => a.interact.mode.startTrustlineFlow(), (a) => { a.interact.mode.cancel(); a.interact.mode.startTrustlineFlowWithFrom('B') }],
    ['"Inspect edge" / a line clicked, over an open flow', (a) => a.interact.mode.startPaymentFlow(), (a) => { a.interact.mode.cancel(); a.interact.mode.selectEdge(keyEdge('R1_NODE', 'B'), { x: 10, y: 10 }) }],
    ['node card ✏️: a line of the participant chosen to edit', (a) => a.interact.mode.startPaymentFlow(), (a) => { a.interact.mode.cancel(); a.interact.mode.selectTrustline('R1_NODE', 'B') }],
    ['canvas: a participant picked in the flow', (a) => a.interact.mode.startPaymentFlow(), (a) => a.interact.mode.selectNode('B')],
  ]
  for (const [name, open, act] of ENTRIES) {
    it(`P2-2: ${name} - pauses the flow brought back by "Поток"`, async () => {
      const app = await openFlowResumed(open)
      act(app)
      await settle()
      expect(pauseRun, 'the entry paused again').toHaveBeenCalledTimes(2)
      expect(app.real.runStatus?.state).toBe('paused')
    })
  }

  it('P2-2 counter-check: a cleared field (null: Escape-back, the window controller) and cancel are not entries and resume nothing', async () => {
    const app = await openFlowResumed((a) => a.interact.mode.startPaymentFlowWithFrom('B'))
    app.interact.mode.setPaymentToPid(null)
    app.interact.mode.setPaymentFromPid(null)
    app.interact.mode.cancel()
    await settle()
    expect(pauseRun, 'no pause for a cleared field or a cancel').toHaveBeenCalledTimes(1)
    expect(resumeRun, 'resumed only by the explicit resume').toHaveBeenCalledTimes(1)
    expect(app.real.runStatus?.state).toBe('running')
  })
})
