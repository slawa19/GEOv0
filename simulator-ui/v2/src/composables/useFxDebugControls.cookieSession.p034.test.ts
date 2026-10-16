/**
 * 034 S8, F-034-D7 (reproducer of T3404): the `ui=demo` buttons work for a cookie session.
 *
 * An anonymous visitor has no access token: the session is the `geo_sim_sid` cookie (`credentials: 'include'` in the
 * transport), and `startRun` already has no token guard for that reason. The demo buttons refused before sending
 * anything - `Missing access token`.
 *
 * Not judged here: whether the server accepts the action (`SIMULATOR_ACTIONS_ENABLE`, ownership of the run).
 */
import { effectScope, reactive, ref, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { actionClearingOnce, actionTxOnce } from '../api/simulatorApi'
import { useFxDebugControls } from './useFxDebugControls'

vi.mock('../api/simulatorApi', () => ({
  actionTxOnce: vi.fn(async () => ({ ok: true })),
  actionClearingOnce: vi.fn(async () => ({ ok: true })),
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

beforeEach(() => {
  vi.mocked(actionTxOnce).mockClear()
  vi.mocked(actionClearingOnce).mockClear()
})

afterEach(() => {
  scope?.stop()
  scope = null
  window.localStorage.clear()
})

describe('F-034-D7: the demo buttons without an access token (cookie session)', () => {
  it('Single Tx sends the action to the run', async () => {
    const s = stand({ accessToken: '', runId: 'R' })

    await s.controls.runTxOnce()

    expect(actionTxOnce).toHaveBeenCalledTimes(1)
    expect(vi.mocked(actionTxOnce).mock.calls[0]?.[1]).toBe('R')
    expect(vi.mocked(actionTxOnce).mock.calls[0]?.[2]).toMatchObject({ equivalent: 'UAH' })
  })

  it('Run Clearing sends the action to the run', async () => {
    const s = stand({ accessToken: '', runId: 'R' })

    await s.controls.runClearingOnce()

    expect(actionClearingOnce).toHaveBeenCalledTimes(1)
    expect(vi.mocked(actionClearingOnce).mock.calls[0]?.[1]).toBe('R')
  })

  it('with no run yet, the paused demo run is started and the action goes to it', async () => {
    const s = stand({ accessToken: '', runId: null })

    await s.controls.runTxOnce()

    expect(s.startRun).toHaveBeenCalledWith({ pauseImmediately: true })
    expect(vi.mocked(actionTxOnce).mock.calls[0]?.[1]).toBe('R_NEW')
  })
})

describe('F-034-D7: what stays', () => {
  it('CONTRACT: with a token the action is sent with it', async () => {
    const s = stand({ accessToken: 'tok', runId: 'R' })

    await s.controls.runTxOnce()

    expect(vi.mocked(actionTxOnce).mock.calls[0]?.[0]).toEqual({ apiBase: 'http://sim.test/api/v1', accessToken: 'tok' })
  })

  it('CONTRACT: with FX debug off the buttons send nothing', async () => {
    const s = stand({ accessToken: '', runId: 'R', fxDebugEnabled: false })

    await s.controls.runTxOnce()
    await s.controls.runClearingOnce()

    expect(actionTxOnce).not.toHaveBeenCalled()
    expect(actionClearingOnce).not.toHaveBeenCalled()
  })

  it('CONTRACT: a run that could not be started is an error, not a silent no-op', async () => {
    const s = stand({ accessToken: 'tok', runId: null })
    s.startRun.mockImplementation(async () => {
      s.real.lastError = 'HTTP 503 Service Unavailable for /simulator/runs'
    })

    await expect(s.controls.runTxOnce()).rejects.toThrow('Failed to start run for FX debug: HTTP 503')
    expect(actionTxOnce).not.toHaveBeenCalled()
    expect(s.controls.busy.value).toBe(false)
  })
})
