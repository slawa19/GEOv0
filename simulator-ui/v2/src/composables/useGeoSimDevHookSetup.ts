import { installGeoSimDevHook } from '../dev/geoSimDevHook'
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
  function setupDevHook() {
    installGeoSimDevHook({
      isDev: opts.isDev,
      isTestMode: opts.isTestMode,
      isWebDriver: opts.isWebDriver,
      getState: opts.getState,
      fxState: opts.fxState as any,
      runTxOnce: opts.runTxOnce,
      runClearingOnce: opts.runClearingOnce,
    })
  }

  return { setupDevHook }
}
