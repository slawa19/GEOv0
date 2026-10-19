import { describe, expect, it, vi } from 'vitest'

import { getScenarioPreview, getSnapshot } from '../api/simulatorApi'
import { __autoBootstrapMaybeFillUiError, loadStrictRunRecoverySnapshot } from './useSimulatorApp'
import { t } from '../i18n/t'
import { runErrorLineLog, type RunErrorLine } from '../utils/runErrorClassification'

vi.mock('../api/simulatorApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulatorApi')>()
  return {
    ...actual,
    getSnapshot: vi.fn(),
    getScenarioPreview: vi.fn(),
  }
})

describe('__autoBootstrapMaybeFillUiError()', () => {
  // 048 S3 (adversarial pass F4): rewritten, not removed - a failed opening is a failure of the RUN: it fills the run's error
  // line (was: the scene's `state.error`, the overlay over the canvas), as a sentence; "Auto-start failed: boom" is the console line.
  it('fills the run error line when best-effort fails and both real.lastError/state.error are empty', () => {
    const state = { error: '' }
    const real: { lastError: string; lastErrorLine?: RunErrorLine | null } = { lastError: '' }

    __autoBootstrapMaybeFillUiError({
      state,
      real,
      err: new Error('boom'),
    })

    expect(real.lastError).toBe(t('failure.viewFailed'))
    expect(runErrorLineLog(real.lastErrorLine)).toBe('Auto-start failed: boom')
    expect(state.error, 'not the scene overlay').toBe('')
  })

  it('does not overwrite when real.lastError is already set (lower level already populated error)', () => {
    const state = { error: '' }
    const real = { lastError: 'HTTP 401' }

    __autoBootstrapMaybeFillUiError({
      state,
      real,
      err: new Error('boom'),
    })

    expect(real.lastError).toBe('HTTP 401')
    expect(state.error).toBe('')
  })
})

describe('loadStrictRunRecoverySnapshot()', () => {
  it('rejects a run snapshot failure without consulting scenario or empty fallbacks', async () => {
    vi.mocked(getSnapshot).mockRejectedValueOnce(new Error('HTTP 503 run snapshot unavailable'))

    await expect(loadStrictRunRecoverySnapshot({
      apiBase: 'http://sim.test/api/v1',
      accessToken: 'token',
      runId: 'run-1',
      equivalent: 'eur',
    })).rejects.toThrow('HTTP 503 run snapshot unavailable')

    expect(getSnapshot).toHaveBeenCalledWith(
      { apiBase: 'http://sim.test/api/v1', accessToken: 'token' },
      'run-1',
      'EUR',
    )
    expect(getScenarioPreview).not.toHaveBeenCalled()
  })
})

