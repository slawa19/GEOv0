/**
 * 044 stage 2 - the visitor's side of "a run of one's own" on the real composable (API mocked at the module boundary):
 * the visitor's tempo (15) against the operator's (30); a tempo below 1 is refused on Start and Apply (BACKLOG 043-19).
 */
import { effectScope, nextTick, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/http'
import { setUiLocale } from '../i18n/locale'
import { connectSse } from '../api/sse'
import {
  createRun, getActiveRun, getRun, getScenarioPreview, getSnapshot, listScenarios, pauseRun, resumeRun, setIntensity, stopRun,
} from '../api/simulatorApi'
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
    createRun: vi.fn(),
    pauseRun: vi.fn(),
    resumeRun: vi.fn(),
    stopRun: vi.fn(),
    setIntensity: vi.fn(),
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

const asVisitor = () => window.localStorage.setItem('geo.sim.v2.accessToken', '')

describe('044 stage 2: the visitor tempo', () => {
  it('a visitor (no admin token): the opening run starts at the visitor tempo 15, and the field shows 15', async () => {
    asVisitor()
    const app = boot()
    await settle()
    expect(createRun).toHaveBeenCalledTimes(1)
    expect(vi.mocked(createRun).mock.calls[0]![1]).toMatchObject({ intensity_percent: 15 })
    expect(app.real.intensityPercent, 'the tempo field says what the run runs at').toBe(15)
  })

  it("the operator (an admin token): the opening run and Start keep 30", async () => {
    window.localStorage.setItem('geo.sim.v2.accessToken', 'an-admin-token')
    const app = boot()
    await settle()
    expect(vi.mocked(createRun).mock.calls[0]![1]).toMatchObject({ intensity_percent: 30 })
    expect(app.real.intensityPercent).toBe(30)
  })

  it('a participant JWT is not the operator: 15', async () => {
    window.localStorage.setItem('geo.sim.v2.accessToken', 'a.b.c')
    boot()
    await settle()
    expect(vi.mocked(createRun).mock.calls[0]![1]).toMatchObject({ intensity_percent: 15 })
  })

  it("a stored preference still wins for the visitor (55)", async () => {
    asVisitor()
    window.localStorage.setItem('geo.sim.v2.intensityPercent', '55')
    boot()
    await settle()
    expect(vi.mocked(createRun).mock.calls[0]![1]).toMatchObject({ intensity_percent: 55 })
  })
})

describe('044 stage 2 (BACKLOG 043-19): a tempo of 0 typed by hand starts no silent run', () => {
  it('Start with 0: no create, a readable hint on the error line', async () => {
    storedRun('R_OLD', 'stopped')
    vi.mocked(createRun).mockRejectedValueOnce(new ApiError('HTTP 503 for /simulator/runs', { status: 503 }))
    document.documentElement.lang = 'ru'
    try {
      const app = boot()
      await settle()
      vi.mocked(createRun).mockClear()
      app.realActions.setIntensityPercent(0)
      await app.realActions.startRun()
      await settle()
      expect(createRun, 'no run at 0').not.toHaveBeenCalled()
      expect(app.real.lastError).toBe('Темп 0 — прогон без платежей: укажите темп от 1 до 100. Остановить платежи — пауза.')
      // counter-check: the same Start with 1 creates
      app.realActions.setIntensityPercent(1)
      await app.realActions.startRun()
      await settle()
      expect(vi.mocked(createRun).mock.calls[0]![1]).toMatchObject({ intensity_percent: 1 })
    } finally {
      document.documentElement.lang = ''
    }
  })

  it('Apply with 0 on a live run: the intensity is not sent, the hint is shown', async () => {
    const app = boot()
    await settle()
    expect(app.real.runStatus?.state).toBe('running')
    app.realActions.setIntensityPercent(0)
    await app.realActions.applyIntensity()
    expect(setIntensity, 'no intensity request').not.toHaveBeenCalled()
    expect(app.real.lastError).toContain('A tempo of 0 makes no payments')
  })
})

// §15 Codex P2 (046 wave 1, 2026-10-18): the lines of tempo 0 and of the idle stop are kept in the error line; a switch of
// the language remakes them from their cause (they were stored translated and stayed in the old language).
describe('046 wave 1: the tempo-0 and idle-stop lines follow a language switch', () => {
  afterEach(() => setUiLocale('en'))

  it('tempo 0: RU -> EN -> RU', async () => {
    storedRun('R_OLD', 'stopped')
    vi.mocked(createRun).mockRejectedValueOnce(new ApiError('HTTP 503 for /simulator/runs', { status: 503 }))
    setUiLocale('ru')
    const app = boot()
    await settle()
    app.realActions.setIntensityPercent(0)
    await app.realActions.startRun()
    await settle()
    expect(app.real.lastError).toBe('Темп 0 — прогон без платежей: укажите темп от 1 до 100. Остановить платежи — пауза.')
    setUiLocale('en')
    await settle()
    expect(app.real.lastError).toBe('A tempo of 0 makes no payments: enter a tempo from 1 to 100. To stop the payments, pause.')
    setUiLocale('ru')
    await settle()
    expect(app.real.lastError).toBe('Темп 0 — прогон без платежей: укажите темп от 1 до 100. Остановить платежи — пауза.')
  })

  it('the idle stop (made by the run status pipeline): RU -> EN', async () => {
    storedRun('R1', 'running')
    let endStream: () => void = () => undefined
    vi.mocked(connectSse).mockImplementationOnce(() => new Promise<void>((resolve) => { endStream = resolve }))
    setUiLocale('ru')
    const app = boot()
    await settle()
    runs.set('R1', 'stopped')
    vi.mocked(getRun).mockImplementation((async (_c: unknown, id: string) => ({ ...status(id), stop_source: 'server', stop_reason: 'idle_no_subscribers' })) as never)
    endStream()
    await settle()
    expect(app.real.lastError).toBe('Прогон остановлен сервером: его долго никто не смотрел. Чтобы запустить снова, перезагрузите страницу.')
    setUiLocale('en')
    await settle()
    expect(app.real.lastError).toBe('The server stopped the run: nobody watched it for a long time. To start again, reload the page.')
  })
})

describe('044 stage 2 (decision 2): a run the server stopped for idleness says so', () => {
  it('an open tab whose stream ends on a run stopped by the idle stop: a readable line, not a raw reason', async () => {
    storedRun('R1', 'running')
    let endStream: () => void = () => undefined
    vi.mocked(connectSse).mockImplementationOnce(() => new Promise<void>((resolve) => { endStream = resolve }))
    document.documentElement.lang = 'ru'
    try {
      const app = boot()
      await settle()
      expect(app.real.runStatus?.state).toBe('running')
      runs.set('R1', 'stopped')
      vi.mocked(getRun).mockImplementation((async (_c: unknown, id: string) => ({ ...status(id), stop_source: 'server', stop_reason: 'idle_no_subscribers' })) as never)
      endStream()
      await settle()
      expect(app.real.runStatus?.state).toBe('stopped')
      // 2026-10-18 (merge into 046 wave 1): from the dictionary (`runLine.idleStopped`); the way back named is the reload -
      // the earlier text pointed at Start under «Подробнее», mixing the languages.
      expect(app.real.lastError).toBe('Прогон остановлен сервером: его долго никто не смотрел. Чтобы запустить снова, перезагрузите страницу.')
      expect(createRun, 'an open tab does not start a run behind the person').not.toHaveBeenCalled()
    } finally {
      document.documentElement.lang = ''
    }
  })

  it('counter-check: a run stopped by a person (user_stop) gets no such line; a returning visitor gets a new run', async () => {
    storedRun('R1', 'stopped')
    vi.mocked(getRun).mockImplementation((async (_c: unknown, id: string) => ({ ...status(id), stop_source: 'server', stop_reason: 'idle_no_subscribers' })) as never)
    const app = boot()
    await settle()
    expect(createRun, 'the returning visitor (page load) gets a live run: the stopped branch starts').toHaveBeenCalledTimes(1)
    expect(app.real.runId).toBe('R_NEW')
    scope?.stop()
    window.localStorage.clear()
    storedRun('R2', 'running')
    let endStream: () => void = () => undefined
    vi.mocked(connectSse).mockImplementationOnce(() => new Promise<void>((resolve) => { endStream = resolve }))
    vi.mocked(getRun).mockImplementation((async (_c: unknown, id: string) => status(id)) as never)
    const other = boot()
    await settle()
    runs.set('R2', 'stopped')
    vi.mocked(getRun).mockImplementation((async (_c: unknown, id: string) => ({ ...status(id), stop_source: 'ui', stop_reason: 'user_stop' })) as never)
    endStream()
    await settle()
    expect(other.real.runStatus?.state).toBe('stopped')
    expect(other.real.lastError).toBe('')
  })
})

describe('044 stage 2: the opening edge paths of BACKLOG 043-23 and 043-24', () => {
  it('043-23: Stop during the first load does not produce a new run', async () => {
    storedRun('R1', 'running')
    let releaseSnapshot: () => void = () => undefined
    vi.mocked(getSnapshot).mockImplementationOnce(
      (async (_c: unknown, id: string) => {
        await new Promise<void>((resolve) => { releaseSnapshot = resolve })
        return snap(`${id}_NODE`)
      }) as never,
    )
    const app = boot()
    await settle()
    expect(app.state.loading, 'the first snapshot is still loading').toBe(true)
    expect(app.real.runStatus?.state).toBe('running')
    await app.realActions.stop()
    await settle()
    expect(stopRun).toHaveBeenCalledTimes(1)
    expect(app.real.runStatus?.state).toBe('stopped')
    releaseSnapshot()
    await settle()
    expect(app.state.loading, 'the opening is over (non-vacuity: the autostart had its moment)').toBe(false)
    expect(createRun, 'the run the person stopped is not replaced behind them').not.toHaveBeenCalled()
  })

  it('043-23 counter-check: without the Stop, the same opening attaches the running run and starts nothing', async () => {
    storedRun('R1', 'running')
    const app = boot()
    await settle()
    expect(app.state.loading).toBe(false)
    expect(createRun).not.toHaveBeenCalled()
    expect(app.real.runId).toBe('R1')
  })

  it('043-24: a manual entry while the found run\'s status is unknown does not lose its pause', async () => {
    storedRun('R1', 'running')
    vi.mocked(getRun).mockRejectedValueOnce(new ApiError('HTTP 500 for /simulator/runs/R1', { status: 500 }))
    const app = boot()
    await settle()
    expect([app.real.runId, app.real.runStatus], 'a run is held, its status unknown').toEqual(['R1', null])
    expect(createRun).not.toHaveBeenCalled()
    await app.realActions.pauseForManualAction()
    expect(pauseRun, 'nothing to pause yet').not.toHaveBeenCalled()
    // The run's state becomes known: it is running (a later read, as the stream's reconnect makes).
    app.real.runStatus = status('R1') as never
    await settle()
    expect(pauseRun, 'the pause asked before the state was known is applied once it is known').toHaveBeenCalledTimes(1)
  })

  it('043-24 counter-check: the found run turns out paused or stopped - nothing more is asked', async () => {
    for (const state of ['paused', 'stopped']) {
      scope?.stop()
      window.localStorage.clear()
      vi.mocked(pauseRun).mockClear()
      storedRun('R1', 'running')
      vi.mocked(getRun).mockRejectedValueOnce(new ApiError('HTTP 500 for /simulator/runs/R1', { status: 500 }))
      const app = boot()
      await settle()
      await app.realActions.pauseForManualAction()
      runs.set('R1', state)
      app.real.runStatus = status('R1') as never
      await settle()
      app.real.runStatus = { ...status('R1'), state: 'running' } as never  // a later running is not an old pause
      await settle()
      expect(pauseRun, state).not.toHaveBeenCalled()
    }
  })
})

// 048 S2 (amendment 9): "admin stop-one" and "admin stop-all" during the opening are gone with "Admin ▾" (no control of
// the page triggers them; a removed contract). The Stop of the run row is `043-23` above; the counter-check stays.
describe('044 stage 2 (Codex §15): every stop the person triggers during the opening spends its pending start', () => {
  /** The opening is held on the scenario catalogue; the stored run R1 is running until the person stops it. */
  async function holdOpening() {
    storedRun('R1', 'running')
    let releaseCatalogue: () => void = () => undefined
    vi.mocked(listScenarios).mockImplementationOnce(
      (async () => {
        await new Promise<void>((resolve) => { releaseCatalogue = resolve })
        return { api_version: 'simulator-api/1', items: [{ scenario_id: 'sc1', label: 'Scenario 1' }] }
      }) as never,
    )
    const app = boot()
    await settle()
    expect(app.state.loading, 'the opening is held').toBe(true)
    return { app, release: () => releaseCatalogue() }
  }

  it('counter-check: a run stopped by someone else (not on this page) is replaced at the opening, as before', async () => {
    const { app, release } = await holdOpening()
    runs.set('R1', 'stopped') // e.g. the server's idle stop
    release()
    await settle()
    expect(app.state.loading).toBe(false)
    expect(createRun).toHaveBeenCalledTimes(1)
  })
})

describe('044 stage 2 (Codex §15 P3): the deferred manual pause is deduplicated like the immediate one', () => {
  it('unknown status -> manual action -> running -> deferred pause in flight -> another manual action: ONE pause', async () => {
    storedRun('R1', 'running')
    vi.mocked(getRun).mockRejectedValueOnce(new ApiError('HTTP 500 for /simulator/runs/R1', { status: 500 }))
    let answerPause: () => void = () => undefined
    vi.mocked(pauseRun).mockImplementation(
      (async (_c: unknown, id: string) => {
        await new Promise<void>((resolve) => { answerPause = resolve })
        runs.set(id, 'paused')
        return status(id)
      }) as never,
    )
    const app = boot()
    await settle()
    await app.realActions.pauseForManualAction()
    app.real.runStatus = status('R1') as unknown as typeof app.real.runStatus // the state becomes known: running -> the deferred pause starts
    await settle()
    expect(pauseRun).toHaveBeenCalledTimes(1)
    void app.realActions.pauseForManualAction() // a second manual action before the first pause answered
    await settle()
    expect(pauseRun, 'the second action joins the pause in flight').toHaveBeenCalledTimes(1)
    answerPause()
    await settle()
    expect(app.real.runStatus?.state).toBe('paused')
  })
})
