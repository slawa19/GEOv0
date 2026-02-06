import type { Ref } from 'vue'

import type { GraphSnapshot } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'
import type { Quality } from '../types/uiPrefs'
import type { VizMapping } from '../vizMapping'

import { useAppFxOverlays } from './useAppFxOverlays'
import { useAppRenderLoop } from './useAppRenderLoop'

export function useAppFxAndRender(opts: {
  // DOM
  canvasEl: Ref<HTMLCanvasElement | null>
  fxCanvasEl: Ref<HTMLCanvasElement | null>
  hostEl: Ref<HTMLDivElement | null>

  // scene + layout
  getSnapshot: () => GraphSnapshot | null
  getLayout: () => { w: number; h: number; nodes: LayoutNode[]; links: LayoutLink[] }
  getLayoutNodes: () => LayoutNode[]
  getLayoutNodeById: (id: string) => LayoutNode | undefined

  // camera
  getCamera: () => { panX: number; panY: number; zoom: number }
  worldToScreen: (x: number, y: number) => { x: number; y: number }

  // node sizing
  sizeForNode: (n: LayoutNode) => { w: number; h: number }

  // flags + prefs
  isTestMode: () => boolean
  isWebDriver: () => boolean
  getQuality: () => Quality

  // flash + overlays
  getFlash: () => number
  setFlash: (v: number) => void

  mapping: VizMapping

  // selection
  getSelectedNodeId: () => string | null

  // render LOD
  getLinkLod: () => 'focus' | 'full'
  getHiddenNodeId: () => string | null

  // hooks
  beforeDraw: () => void

  // Optional: hint whether the scene is actively animating (physics, pan/zoom, demo playback).
  isAnimating?: () => boolean

  // Optional: hint that the user is actively interacting (wheel/click/drag hold window).
  // Used to enable Interaction Quality overrides (skip blur-heavy paths + clamp DPR).
  isInteracting?: () => boolean

  // Optional: smooth interaction intensity 0.0–1.0 with easing transitions.
  getInteractionIntensity?: () => number

  // Optional: hint that the browser is in software-only rendering mode.
  isSoftwareMode?: () => boolean
}) {
  // Late-bound: we create FX overlays first (they need timers), then create render loop,
  // then bind wakeUp so scheduled FX timers can wake the loop even after deep idle.
  let wakeUp: ((source?: 'user' | 'animation') => void) | undefined

  const fxOverlays = useAppFxOverlays<LayoutNode>({
    getLayoutNodeById: opts.getLayoutNodeById,
    sizeForNode: opts.sizeForNode,
    getCameraZoom: () => opts.getCamera().zoom,
    setFlash: opts.setFlash,
    isWebDriver: opts.isWebDriver,
    getLayoutNodes: opts.getLayoutNodes,
    worldToScreen: opts.worldToScreen,
    // Important: FX timers must wake up render loop before mutating FX state.
    // This prevents missed frames when the loop is in deep idle.
    wakeUp: () => wakeUp?.(),
  })

  const renderLoop = useAppRenderLoop({
    canvasEl: opts.canvasEl,
    fxCanvasEl: opts.fxCanvasEl,
    getSnapshot: opts.getSnapshot,
    getLayout: opts.getLayout,
    getCamera: opts.getCamera,
    isTestMode: opts.isTestMode,
    getQuality: opts.getQuality,
    getFlash: opts.getFlash,
    setFlash: opts.setFlash,
    pruneActiveEdges: fxOverlays.pruneActiveEdges,
    pruneActiveNodes: fxOverlays.pruneActiveNodes,
    pruneFloatingLabels: fxOverlays.pruneFloatingLabels,
    mapping: opts.mapping,
    fxState: fxOverlays.fxState,
    getSelectedNodeId: opts.getSelectedNodeId,
    activeEdges: fxOverlays.activeEdges,
    activeNodes: fxOverlays.activeNodes,
    getLinkLod: opts.getLinkLod,
    getHiddenNodeId: opts.getHiddenNodeId,
    beforeDraw: opts.beforeDraw,
    isAnimating: opts.isAnimating,
    isInteracting: opts.isInteracting,
    getInteractionIntensity: opts.getInteractionIntensity,
    isSoftwareMode: opts.isSoftwareMode,
  })

  // Bind after render loop is created.
  wakeUp = renderLoop.wakeUp

  return {
    // fx overlays
    fxState: fxOverlays.fxState,
    hoveredEdge: fxOverlays.hoveredEdge,
    clearHoveredEdge: fxOverlays.clearHoveredEdge,
    activeEdges: fxOverlays.activeEdges,
    addActiveEdge: fxOverlays.addActiveEdge,
    pruneActiveEdges: fxOverlays.pruneActiveEdges,

    activeNodes: fxOverlays.activeNodes,
    addActiveNode: fxOverlays.addActiveNode,
    pruneActiveNodes: fxOverlays.pruneActiveNodes,
    pushFloatingLabel: fxOverlays.pushFloatingLabel,
    pruneFloatingLabels: fxOverlays.pruneFloatingLabels,
    resetOverlays: fxOverlays.resetOverlays,
    floatingLabelsViewFx: fxOverlays.floatingLabelsViewFx,
    scheduleTimeout: fxOverlays.scheduleTimeout,
    clearScheduledTimeouts: fxOverlays.clearScheduledTimeouts,

    // render loop
    ensureRenderLoop: renderLoop.ensureRenderLoop,
    stopRenderLoop: renderLoop.stopRenderLoop,
    renderOnce: renderLoop.renderOnce,
    wakeUp: renderLoop.wakeUp,
  }
}
