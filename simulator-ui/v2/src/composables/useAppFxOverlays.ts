import { createFxState, resetFxState } from '../render/fxRenderer'
import { createTimerRegistry } from '../demo/timerRegistry'
import { useFloatingLabelsViewFx } from './useFloatingLabelsViewFx'
import { type LayoutNodeLike, useOverlayState } from './useOverlayState'

export function useAppFxOverlays<N extends LayoutNodeLike>(deps: {
  getLayoutNodeById: (id: string) => N | undefined
  sizeForNode: (n: N) => { w: number; h: number }
  getCameraZoom: () => number
  setFlash: (v: number) => void

  // Optional: called before any scheduled FX timer callback.
  // This is used to ensure the render loop is awake even after deep idle.
  wakeUp?: () => void

  isWebDriver: () => boolean
  getLayoutNodes: () => N[]
  worldToScreen: (x: number, y: number) => { x: number; y: number }
}) {
  const fxState = createFxState()
  const timers = createTimerRegistry()

  // Dev-only diagnostics (plan §10): expose timer stats on localhost.
  // This helps validate keepCritical behavior during snapshot refreshes.
  try {
    const host = globalThis?.location?.hostname
    if (host === 'localhost' || host === '127.0.0.1') {
      ;(globalThis as any).__geo_timers_stats = () => timers.getStats()
    }
  } catch {
    // ignore
  }

  const overlayState = useOverlayState<N>({
    getLayoutNodeById: deps.getLayoutNodeById,
    sizeForNode: deps.sizeForNode,
    getCameraZoom: deps.getCameraZoom,
    setFlash: deps.setFlash,
    resetFxState: () => resetFxState(fxState),
  })

  const floatingLabelsViewFx = useFloatingLabelsViewFx({
    getFloatingLabelsView: () => overlayState.floatingLabelsView.value,
    isWebDriver: deps.isWebDriver,
    getLayoutNodes: deps.getLayoutNodes,
    sizeForNode: deps.sizeForNode,
    worldToScreen: deps.worldToScreen,
  }).floatingLabelsViewFx

  function scheduleTimeout(fn: () => void, delayMs: number, opts?: { critical?: boolean }) {
    // Ensure render loop is awake before any FX state mutation emitted by the timer.
    return timers.schedule(() => {
      deps.wakeUp?.()
      fn()
    }, delayMs, opts)
  }

  function clearScheduledTimeouts(opts?: { keepCritical?: boolean }) {
    timers.clearAll(opts)
  }

  return {
    fxState,

    hoveredEdge: overlayState.hoveredEdge,
    clearHoveredEdge: overlayState.clearHoveredEdge,

    activeEdges: overlayState.activeEdges,
    addActiveEdge: overlayState.addActiveEdge,
    pruneActiveEdges: overlayState.pruneActiveEdges,

    activeNodes: overlayState.activeNodes,
    addActiveNode: overlayState.addActiveNode,
    pruneActiveNodes: overlayState.pruneActiveNodes,

    pushFloatingLabel: overlayState.pushFloatingLabel,
    pruneFloatingLabels: overlayState.pruneFloatingLabels,

    resetOverlays: overlayState.resetOverlays,

    floatingLabelsViewFx,

    scheduleTimeout,
    clearScheduledTimeouts,
  }
}
