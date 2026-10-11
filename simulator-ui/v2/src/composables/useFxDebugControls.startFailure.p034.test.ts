/**
 * 034 S8, F-034-D14 (reproducer of T3404): a demo run that failed to start is not "attached" to somebody's run
 * because the error text contains the word `conflict`.
 *
 * `startRun` owns the 409 answer: it reads the status of the error and attaches to the active run itself
 * (`useSimulatorRealMode.test.ts`, "attaches to active run when createRun returns HTTP 409"). What reaches the demo
 * controls afterwards is only `real.lastError` - a text with the server's body in it.
 */
import { effectScope, reactive, ref, type EffectScope } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { actionTxOnce, getActiveRun } from '../api/simulatorApi'
import { useFxDebugControls } from './useFxDebugControls'

vi.mock('../api/simulatorApi', () => ({
  actionTxOnce: vi.fn(async () => ({ ok: true })),
  actionClearingOnce: vi.fn(async () => ({ ok: true })),
  getActiveRun: vi.fn(async () => ({ run_id: 'SOMEBODY_ELSES_RUN' })),
}))

let scope: EffectScope | null = null

function stand(startRunLeaves: { runId: string | null; lastError: string }) {
  const real = reactive({
    apiBase: 'http://sim.test/api/v1',
    accessToken: 'tok',
    selectedScenarioId: 'sc1' as string | null,
    runId: null as string | null,
    runStatus: null as { state?: string | null } | null,
    lastError: '',
    sseState: 'open',
  })
  scope = effectScope()
  const controls = scope.run(() =>
    useFxDebugControls({
      isFxDebugEnabled: ref(true),
      real,
      effectiveEq: ref('UAH'),
      markDemoActivity: () => undefined,
      realMode: {
        refreshRunStatus: async () => undefined,
        resetStaleRun: () => undefined,
        refreshScenarios: async () => undefined,
        startRun: async () => {
          real.runId = startRunLeaves.runId
          real.lastError = startRunLeaves.lastError
        },
        pause: async () => undefined,
        refreshSnapshot: async () => undefined,
      },
    }),
  )!
  return { real, controls }
}

afterEach(() => {
  scope?.stop()
  scope = null
  window.localStorage.clear()
  vi.mocked(actionTxOnce).mockClear()
  vi.mocked(getActiveRun).mockClear()
})

describe('F-034-D14: a demo run that did not start', () => {
  // T3404: red on origin/main because of F-034-D14; S8 removes `.fails` together with the fix.
  it.fails('a 500 whose text says "conflict" is a failure, not a reason to act on the active run', async () => {
    const s = stand({
      runId: null,
      lastError: 'HTTP 500 Internal Server Error for /simulator/runs: serialization conflict, retry later',
    })

    await expect(s.controls.runTxOnce()).rejects.toThrow('Failed to start run for FX debug: HTTP 500')

    expect(s.real.runId, 'the controls attached to a run on the strength of a word in an error text').toBeNull()
    expect(actionTxOnce, 'an action was sent to a run this start did not create').not.toHaveBeenCalled()
  })

  it('CONTRACT: when startRun attached to the active run (its own 409 handling), the action goes to that run', async () => {
    const s = stand({ runId: 'ACTIVE', lastError: '' })

    await s.controls.runTxOnce()

    expect(vi.mocked(actionTxOnce).mock.calls[0]?.[1]).toBe('ACTIVE')
  })

  // T3404: red on origin/main because of F-034-D14; S8 removes `.fails` together with the fix.
  it.fails('a 409 that startRun itself could not resolve is reported too - the click can be repeated', async () => {
    const s = stand({ runId: null, lastError: 'HTTP 409 Conflict for /simulator/runs' })

    await expect(s.controls.runTxOnce()).rejects.toThrow('Failed to start run for FX debug: HTTP 409 Conflict')
    expect(actionTxOnce).not.toHaveBeenCalled()
  })
})
