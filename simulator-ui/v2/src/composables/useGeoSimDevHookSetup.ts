import { installGeoSimDevHook, uninstallGeoSimDevHook } from '../dev/geoSimDevHook'
import type { FxState } from '../render/fxRenderer'
import type { SimulatorAppState } from '../types/simulatorApp'

export function useGeoSimDevHookSetup(opts: {
  isDev: () => boolean
  isTestMode: () => boolean
  isWebDriver: () => boolean
  getState: () => SimulatorAppState
  getCamera?: () => { panX: number; panY: number; zoom: number }
  fxState: FxState
  runTxOnce: () => void
  runClearingOnce: () => void
  showEdgeTooltip?: (edge: {
    key: string
    fromId: string
    toId: string
    amountText: string
    screenX: number
    screenY: number
    trustLimit?: string | number | null
    used?: string | number | null
    available?: string | number | null
    edgeStatus?: string | null
  }) => void
  hideEdgeTooltip?: () => void
  openNodeCard?: (o: { nodeId: string; anchor: { x: number; y: number } | null }) => void
  openEdgeDetail?: (o: { fromPid: string; toPid: string; anchor: { x: number; y: number } }) => void
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
      getCamera: opts.getCamera,
      fxState: opts.fxState,
      runTxOnce: opts.runTxOnce,
      runClearingOnce: opts.runClearingOnce,
      showEdgeTooltip: opts.showEdgeTooltip,
      hideEdgeTooltip: opts.hideEdgeTooltip,
      openNodeCard: opts.openNodeCard,
      openEdgeDetail: opts.openEdgeDetail,
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
