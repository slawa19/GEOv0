import type { GraphSnapshot } from '../types'
import type { TxUpdatedEvent, ClearingPlanEvent, ClearingDoneEvent } from '../types'
import { installGeoSimDevHook } from '../dev/geoSimDevHook'

type AppState = {
  loading: boolean
  error: string
  sourcePath: string
  eventsPath: string
  snapshot: GraphSnapshot | null
  demoTxEvents: TxUpdatedEvent[]
  demoClearingPlan: ClearingPlanEvent | null
  demoClearingDone: ClearingDoneEvent | null
  selectedNodeId: string | null
  flash: number
}

export function useGeoSimDevHookSetup(opts: {
  isDev: () => boolean
  isTestMode: () => boolean
  isWebDriver: () => boolean
  getState: () => AppState
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
