/**
 * 034 S8, F-034-D7 (reproducer of T3404): the `ui=demo` paused run starts for a cookie session.
 *
 * An anonymous visitor has no access token: the session is the `geo_sim_sid` cookie (`credentials: 'include'` in the
 * transport), and `startRun` already has no token guard for that reason. The demo controls refused before sending
 * anything - `Missing access token`.
 *
 * 042 S1 (2026-10-16): the server actions `tx-once` / `clearing-once` are gone, and with them the half of this file
 * that checked the buttons SENT them (to which run, with which token). What stays is the run they started -
 * `ensureRunSerialized`, which the demo UI's bootstrap still uses - and a check that the buttons now send nothing.
 *
 * Not judged here: whether the server accepts the run (ownership, limits).
 */
import { effectScope, reactive, ref, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useFxDebugControls } from './useFxDebugControls'

vi.mock('../api/simulatorApi', () => ({
  getActiveRun: vi.fn(async () => ({ run_id: null })),
}))

let scope: EffectScope | null = null

function stand(input: { accessToken: string; runId: string | null; fxDebugEnabled?: boolean }) {
  const real = reactive({
    apiBase: 'http://sim.test/api/v1',
    accessToken: input.accessToken,
    selectedScenarioId: 'sc1' as string | null,
    runId: input.runId,
    runStatus: (input.runId ? { state: 'paused' } : null) as { state?: string | null } | null,
    lastError: '',
    sseState: 'open',
  })
  const startRun = vi.fn(async () => {
    real.runId = 'R_NEW'
    real.runStatus = { state: 'paused' }
  })
  scope = effectScope()
  const controls = scope.run(() =>
    useFxDebugControls({
      isFxDebugEnabled: ref(input.fxDebugEnabled ?? true),
      real,
      effectiveEq: ref('UAH'),
      markDemoActivity: () => undefined,
      realMode: {
        refreshRunStatus: async () => undefined,
        resetStaleRun: () => undefined,
        refreshScenarios: async () => undefined,
        startRun,
        pause: async () => undefined,
        refreshSnapshot: async () => undefined,
      },
    }),
  )!
  return { real, controls, startRun }
}

let fetchSpy: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchSpy = vi.fn(async () => new Response('{}', { status: 200 }))
  vi.stubGlobal('fetch', fetchSpy)
})

afterEach(() => {
  scope?.stop()
  scope = null
  window.localStorage.clear()
  vi.unstubAllGlobals()
})

describe('F-034-D7: the demo run without an access token (cookie session)', () => {
  it('with no run yet, the paused demo run is started and its id returned', async () => {
    const s = stand({ accessToken: '', runId: null })

    await expect(s.controls.ensureRunSerialized()).resolves.toBe('R_NEW')

    expect(s.startRun).toHaveBeenCalledWith({ pauseImmediately: true })
  })

  it('CONTRACT: a live run is reused, nothing is started', async () => {
    const s = stand({ accessToken: '', runId: 'R' })

    await expect(s.controls.ensureRunSerialized()).resolves.toBe('R')

    expect(s.startRun).not.toHaveBeenCalled()
  })

  it('CONTRACT: a run that could not be started is an error, not a silent no-op', async () => {
    const s = stand({ accessToken: 'tok', runId: null })
    s.startRun.mockImplementation(async () => {
      s.real.lastError = 'HTTP 503 Service Unavailable for /simulator/runs'
    })

    await expect(s.controls.ensureRunSerialized()).rejects.toThrow('Failed to start run for FX debug: HTTP 503')
  })
})

describe('042 S1: Single Tx and Run Clearing have no server action any more', () => {
  it('they send nothing and start no run, whatever the debug flag says', async () => {
    for (const fxDebugEnabled of [true, false]) {
      const s = stand({ accessToken: 'tok', runId: null, fxDebugEnabled })

      await s.controls.runTxOnce()
      await s.controls.runClearingOnce()

      expect(s.startRun, `fxDebugEnabled=${fxDebugEnabled}`).not.toHaveBeenCalled()
      expect(s.controls.busy.value).toBe(false)
      scope?.stop()
    }
    expect(fetchSpy, 'a request left the demo buttons').not.toHaveBeenCalled()
  })
})
