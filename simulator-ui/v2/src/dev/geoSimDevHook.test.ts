import { describe, expect, it } from 'vitest'
import type { FxState } from '../render/fxRenderer'
import { installGeoSimDevHook } from './geoSimDevHook'

describe('dev/geoSimDevHook', () => {
  it('does nothing when isDev=false', () => {
    const prev = (globalThis as any).window
    ;(globalThis as any).window = {}

    installGeoSimDevHook({
      isDev: () => false,
      isTestMode: () => true,
      isWebDriver: () => false,
      getState: () => ({ loading: false, error: '', snapshot: null }),
      fxState: { sparks: [], edgePulses: [], nodeBursts: [] } as FxState,
      runTxOnce: () => undefined,
      runClearingOnce: () => undefined,
    })

    expect((globalThis as any).window.__geoSim).toBeUndefined()

    ;(globalThis as any).window = prev
  })

  it('installs window.__geoSim when isDev=true', () => {
    const prev = (globalThis as any).window
    ;(globalThis as any).window = {}

    const fxState = { sparks: [], edgePulses: [], nodeBursts: [] } as FxState

    installGeoSimDevHook({
      isDev: () => true,
      isTestMode: () => false,
      isWebDriver: () => true,
      getState: () => ({ loading: true, error: 'oops', snapshot: { ok: 1 } }),
      fxState,
      runTxOnce: () => undefined,
      runClearingOnce: () => undefined,
    })

    const hook = (globalThis as any).window.__geoSim
    expect(hook).toBeTruthy()
    expect(hook.isTestMode).toBe(false)
    expect(hook.isWebDriver).toBe(true)
    expect(hook.loading).toBe(true)
    expect(hook.error).toBe('oops')
    expect(hook.hasSnapshot).toBe(true)
    expect(hook.fxState).toBe(fxState)

    ;(globalThis as any).window = prev
  })
})
