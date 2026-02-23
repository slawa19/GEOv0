import { describe, expect, it } from 'vitest'
import type { FxState } from '../render/fxRenderer'
import { installGeoSimDevHook, uninstallGeoSimDevHook } from './geoSimDevHook'

describe('dev/geoSimDevHook', () => {
  it('does nothing when isDev=false', () => {
    const prev = (globalThis as any).window
    ;(globalThis as any).window = {}

    const cleanup = installGeoSimDevHook({
      isDev: () => false,
      isTestMode: () => true,
      isWebDriver: () => false,
      getState: () => ({ loading: false, error: '', snapshot: null }),
      fxState: { sparks: [], edgePulses: [], nodeBursts: [] } as FxState,
      runTxOnce: () => undefined,
      runClearingOnce: () => undefined,
    })

    expect(cleanup).toBeUndefined()

    expect((globalThis as any).window.__geoSim).toBeUndefined()

    ;(globalThis as any).window = prev
  })

  it('installs window.__geoSim when isDev=true', () => {
    const prev = (globalThis as any).window
    ;(globalThis as any).window = {}

    const fxState = { sparks: [], edgePulses: [], nodeBursts: [] } as FxState

    const cleanup = installGeoSimDevHook({
      isDev: () => true,
      isTestMode: () => false,
      isWebDriver: () => true,
      getState: () => ({ loading: true, error: 'oops', snapshot: { ok: 1 } }),
      fxState,
      runTxOnce: () => undefined,
      runClearingOnce: () => undefined,
    })

    expect(cleanup).toBeTypeOf('function')

    const hook = (globalThis as any).window.__geoSim
    expect(hook).toBeTruthy()
    expect(hook.isTestMode).toBe(false)
    expect(hook.isWebDriver).toBe(true)
    expect(hook.loading).toBe(true)
    expect(hook.error).toBe('oops')
    expect(hook.hasSnapshot).toBe(true)
    expect(hook.fxState).toBe(fxState)

    cleanup?.()
    expect((globalThis as any).window.__geoSim).toBeUndefined()

    ;(globalThis as any).window = prev
  })

  it('cleanup from previous install does not remove a newer hook', () => {
    const prev = (globalThis as any).window
    ;(globalThis as any).window = {}

    const fx1 = { sparks: [], edgePulses: [], nodeBursts: [] } as FxState
    const fx2 = { sparks: [], edgePulses: [], nodeBursts: [] } as FxState

    const cleanup1 = installGeoSimDevHook({
      isDev: () => true,
      isTestMode: () => false,
      isWebDriver: () => false,
      getState: () => ({ loading: false, error: '', snapshot: null }),
      fxState: fx1,
      runTxOnce: () => undefined,
      runClearingOnce: () => undefined,
    })

    const hook1 = (globalThis as any).window.__geoSim
    expect(hook1).toBeTruthy()
    expect(hook1.fxState).toBe(fx1)

    const cleanup2 = installGeoSimDevHook({
      isDev: () => true,
      isTestMode: () => true,
      isWebDriver: () => true,
      getState: () => ({ loading: true, error: 'e', snapshot: { ok: 1 } }),
      fxState: fx2,
      runTxOnce: () => undefined,
      runClearingOnce: () => undefined,
    })

    const hook2 = (globalThis as any).window.__geoSim
    expect(hook2).toBeTruthy()
    expect(hook2).not.toBe(hook1)
    expect(hook2.fxState).toBe(fx2)

    cleanup1?.()
    expect((globalThis as any).window.__geoSim).toBe(hook2)

    cleanup2?.()
    expect((globalThis as any).window.__geoSim).toBeUndefined()

    // Idempotent global uninstall.
    uninstallGeoSimDevHook()

    ;(globalThis as any).window = prev
  })
})
