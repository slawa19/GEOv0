import { describe, expect, it } from 'vitest'

import { __autoBootstrapMaybeFillUiError } from './useSimulatorApp'

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

