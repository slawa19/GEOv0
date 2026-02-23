import { installGeoSimDevHook, uninstallGeoSimDevHook } from '../dev/geoSimDevHook'
import type { SimulatorAppState } from '../types/simulatorApp'

export function useGeoSimDevHookSetup(opts: {
  isDev: () => boolean
  isTestMode: () => boolean
  isWebDriver: () => boolean
  getState: () => SimulatorAppState
  fxState: unknown
  runTxOnce: () => void
  runClearingOnce: () => void
}) {
  let cleanup: (() => void) | undefined

  function setupDevHook() {
    // Ensure a clean slate on repeated setup (HMR / remounts).
    cleanup?.()
    cleanup = undefined

    cleanup = installGeoSimDevHook({
      isDev: opts.isDev,
      isTestMode: opts.isTestMode,
      isWebDriver: opts.isWebDriver,
      getState: opts.getState,
      fxState: opts.fxState as any,
      runTxOnce: opts.runTxOnce,
      runClearingOnce: opts.runClearingOnce,
    })
  }

  function disposeDevHook() {
    cleanup?.()
    cleanup = undefined

    // Extra safety: in case setup changed or cleanup was lost.
    uninstallGeoSimDevHook()
  }

  return { setupDevHook, disposeDevHook }
}
