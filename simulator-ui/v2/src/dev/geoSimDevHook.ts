import type { FxState } from '../render/fxRenderer'

type GeoSimDevHookState = {
  loading: boolean
  error: string
  snapshot: unknown | null
}

type InstallGeoSimDevHookDeps = {
  isDev: () => boolean
  isTestMode: () => boolean
  isWebDriver: () => boolean
  getState: () => GeoSimDevHookState
  fxState: FxState
  runTxOnce: () => Promise<void> | void
  runClearingOnce: () => Promise<void> | void
}

export function installGeoSimDevHook(deps: InstallGeoSimDevHookDeps) {
  if (!deps.isDev()) return
  if (typeof window === 'undefined') return

  ;(window as any).__geoSim = {
    get isTestMode() {
      return deps.isTestMode()
    },
    get isWebDriver() {
      return deps.isWebDriver()
    },
    get loading() {
      return deps.getState().loading
    },
    get error() {
      return deps.getState().error
    },
    get hasSnapshot() {
      return !!deps.getState().snapshot
    },
    fxState: deps.fxState,
    runTxOnce: deps.runTxOnce,
    runClearingOnce: deps.runClearingOnce,
  }
}
