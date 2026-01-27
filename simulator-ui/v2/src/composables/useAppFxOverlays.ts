import { createFxState, resetFxState } from '../render/fxRenderer'
import { createTimerRegistry } from '../demo/timerRegistry'
import { useFloatingLabelsViewFx } from './useFloatingLabelsViewFx'
import { type LayoutNodeLike, useOverlayState } from './useOverlayState'

export function useAppFxOverlays<N extends LayoutNodeLike>(deps: {
  getLayoutNodeById: (id: string) => N | undefined
  sizeForNode: (n: N) => { w: number; h: number }
  getCameraZoom: () => number
  setFlash: (v: number) => void

  isWebDriver: () => boolean
  getLayoutNodes: () => N[]
  worldToScreen: (x: number, y: number) => { x: number; y: number }
}) {
  const fxState = createFxState()
  const timers = createTimerRegistry()

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

  function scheduleTimeout(fn: () => void, delayMs: number) {
    return timers.schedule(fn, delayMs)
  }

  function clearScheduledTimeouts() {
    timers.clearAll()
  }

  return {
    fxState,

    hoveredEdge: overlayState.hoveredEdge,
    clearHoveredEdge: overlayState.clearHoveredEdge,

    activeEdges: overlayState.activeEdges,
    addActiveEdge: overlayState.addActiveEdge,
    pruneActiveEdges: overlayState.pruneActiveEdges,

    pushFloatingLabel: overlayState.pushFloatingLabel,
    pruneFloatingLabels: overlayState.pruneFloatingLabels,

    resetOverlays: overlayState.resetOverlays,

    floatingLabelsViewFx,

    scheduleTimeout,
    clearScheduledTimeouts,
  }
}
