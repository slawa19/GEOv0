import type { Ref } from 'vue'
import type { GraphSnapshot } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'
import { drawBaseGraph, type LayoutLink as RenderLayoutLink } from '../render/baseGraph'
import { type LayoutNode as RenderLayoutNode } from '../render/nodePainter'
import { renderFxFrame, type FxState } from '../render/fxRenderer'
import type { VizMapping } from '../vizMapping'
import { useRenderLoop } from './useRenderLoop'

export function useAppRenderLoop(opts: {
  canvasEl: Ref<HTMLCanvasElement | null>
  fxCanvasEl: Ref<HTMLCanvasElement | null>
  getSnapshot: () => GraphSnapshot | null
  getLayout: () => { w: number; h: number; nodes: LayoutNode[]; links: LayoutLink[] }
  getCamera: () => { panX: number; panY: number; zoom: number }
  isTestMode: () => boolean
  getQuality: () => 'low' | 'med' | 'high'
  getFlash: () => number
  setFlash: (v: number) => void
  pruneActiveEdges: (nowMs: number) => void
  pruneActiveNodes: (nowMs: number) => void
  pruneFloatingLabels: (nowMs: number) => void
  mapping: VizMapping
  fxState: FxState
  getSelectedNodeId: () => string | null
  activeEdges: Map<string, number>
  activeNodes: Set<string>
  getLinkLod: () => 'focus' | 'full'
  getHiddenNodeId: () => string | null
  beforeDraw: () => void

  // Optional: hint whether the scene is actively animating (pan/zoom, physics, playback).
  isAnimating?: () => boolean
}) {
  const renderLoop = useRenderLoop({
    canvasEl: opts.canvasEl,
    fxCanvasEl: opts.fxCanvasEl,
    getSnapshot: opts.getSnapshot,
    getLayout: () => {
      const l = opts.getLayout()
      return {
        w: l.w,
        h: l.h,
        nodes: l.nodes as unknown as RenderLayoutNode[],
        links: l.links as unknown as RenderLayoutLink[],
      }
    },
    getCamera: opts.getCamera,
    isTestMode: opts.isTestMode,
    getQuality: opts.getQuality,
    getFlash: opts.getFlash,
    setFlash: opts.setFlash,
    pruneActiveEdges: opts.pruneActiveEdges,
    pruneActiveNodes: opts.pruneActiveNodes,
    pruneFloatingLabels: opts.pruneFloatingLabels,
    drawBaseGraph,
    renderFxFrame,
    mapping: opts.mapping,
    fxState: opts.fxState,
    getSelectedNodeId: opts.getSelectedNodeId,
    activeEdges: opts.activeEdges,
    activeNodes: opts.activeNodes,
    getLinkLod: opts.getLinkLod,
    getHiddenNodeId: opts.getHiddenNodeId,
    beforeDraw: opts.beforeDraw,
    isAnimating: opts.isAnimating,
  })

  return {
    ensureRenderLoop: renderLoop.ensureRenderLoop,
    stopRenderLoop: renderLoop.stopRenderLoop,
    renderOnce: renderLoop.renderOnce,
    wakeUp: renderLoop.wakeUp,
  }
}
