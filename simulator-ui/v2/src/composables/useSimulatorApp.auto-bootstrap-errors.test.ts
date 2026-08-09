import { describe, expect, it, vi } from 'vitest'

import { getScenarioPreview, getSnapshot } from '../api/simulatorApi'
import { __autoBootstrapMaybeFillUiError, loadStrictRunRecoverySnapshot } from './useSimulatorApp'

vi.mock('../api/simulatorApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulatorApi')>()
  return {
    ...actual,
    getSnapshot: vi.fn(),
    getScenarioPreview: vi.fn(),
  }
})

describe('__autoBootstrapMaybeFillUiError()', () => {
  it('fills state.error when best-effort fails and both real.lastError/state.error are empty', () => {
    const state = { error: '' }
    const real = { lastError: '' }

    __autoBootstrapMaybeFillUiError({
      state,
      real,
      err: new Error('boom'),
    })

    expect(state.error).toBe('Auto-start failed: boom')
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

