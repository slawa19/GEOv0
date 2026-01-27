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
  pruneFloatingLabels: (nowMs: number) => void
  mapping: VizMapping
  fxState: FxState
  getSelectedNodeId: () => string | null
  activeEdges: Set<string>
  getLinkLod: () => 'focus' | 'full'
  getHiddenNodeId: () => string | null
  beforeDraw: () => void
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
    pruneFloatingLabels: opts.pruneFloatingLabels,
    drawBaseGraph,
    renderFxFrame,
    mapping: opts.mapping,
    fxState: opts.fxState,
    getSelectedNodeId: opts.getSelectedNodeId,
    activeEdges: opts.activeEdges,
    getLinkLod: opts.getLinkLod,
    getHiddenNodeId: opts.getHiddenNodeId,
    beforeDraw: opts.beforeDraw,
  })

  return {
    ensureRenderLoop: renderLoop.ensureRenderLoop,
    stopRenderLoop: renderLoop.stopRenderLoop,
    renderOnce: renderLoop.renderOnce,
  }
}
