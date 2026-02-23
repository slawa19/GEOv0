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

function clearGeoSimDevHook(expected?: unknown) {
  const w = (globalThis as any).window as any
  if (!w) return

  if (expected !== undefined && w.__geoSim !== expected) return

  // Ensure we don't keep stale references between HMR / remounts.
  if ('__geoSim' in w) {
    w.__geoSim = undefined
    try {
      delete w.__geoSim
    } catch {
      // Best-effort: some environments may not allow deleting.
    }
  }
}

export function uninstallGeoSimDevHook() {
  clearGeoSimDevHook()
}

export function installGeoSimDevHook(deps: InstallGeoSimDevHookDeps): (() => void) | undefined {
  if (!deps.isDev()) return
  const w = (globalThis as any).window as any
  if (!w) return

  // Idempotent safe re-install: always detach previous instance first.
  uninstallGeoSimDevHook()

  const hook = {
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

  w.__geoSim = hook

  // Cleanup is safe to call even after a newer re-install.
  return () => clearGeoSimDevHook(hook)
}
