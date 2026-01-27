import type { Ref } from 'vue'
import type { GraphNode } from '../types'
import type { LayoutNode } from '../types/layout'
import { useDragPreview } from './useDragPreview'
import { useDragToPinInteraction } from './useDragToPinInteraction'

export function useAppDragToPinAndPreview(opts: {
  dragPreviewEl: Ref<HTMLDivElement | null>
  isTestMode: () => boolean

  pickNodeAt: (clientX: number, clientY: number) => { id: string } | null
  getLayoutNodeById: (id: string) => LayoutNode | null
  setSelectedNodeId: (id: string | null) => void
  clearHoveredEdge: () => void

  clientToScreen: (clientX: number, clientY: number) => { x: number; y: number }
  screenToWorld: (x: number, y: number) => { x: number; y: number }
  getCanvasEl: () => HTMLCanvasElement | null

  renderOnce: () => void

  pinNodeLive: (id: string, x: number, y: number) => void
  commitPinnedPos: (id: string, x: number, y: number) => void
  reheatPhysics: (alpha?: number) => void

  getNodeById: (id: string) => GraphNode | null
  getCamera: () => { panX: number; panY: number; zoom: number }
  sizeForNode: (n: GraphNode) => { w: number; h: number }
  fillForNode: (n: GraphNode) => string
}) {
  let showDragPreviewForNode: (nodeId: string) => void = () => {}
  let scheduleDragPreview: () => void = () => {}
  let hideDragPreview: () => void = () => {}

  const dragToPin = useDragToPinInteraction({
    isEnabled: () => !opts.isTestMode(),
    pickNodeAt: opts.pickNodeAt,
    getLayoutNodeById: opts.getLayoutNodeById,
    setSelectedNodeId: opts.setSelectedNodeId,
    clearHoveredEdge: opts.clearHoveredEdge,
    clientToScreen: opts.clientToScreen,
    screenToWorld: opts.screenToWorld,
    getCanvasEl: opts.getCanvasEl,
    renderOnce: opts.renderOnce,
    pinNodeLive: opts.pinNodeLive,
    commitPinnedPos: opts.commitPinnedPos,
    reheatPhysics: opts.reheatPhysics,
    showDragPreviewForNode: (id) => showDragPreviewForNode(id),
    scheduleDragPreview: () => scheduleDragPreview(),
    hideDragPreview: () => hideDragPreview(),
  })

  const dragPreview = useDragPreview({
    el: opts.dragPreviewEl,
    getDraggedNode: () => dragToPin.dragState.cachedNode,
    getNodeById: opts.getNodeById,
    getCamera: opts.getCamera,
    renderOnce: opts.renderOnce,
    sizeForNode: opts.sizeForNode,
    fillForNode: opts.fillForNode,
  })

  hideDragPreview = dragPreview.hideDragPreview
  showDragPreviewForNode = dragPreview.showDragPreviewForNode
  scheduleDragPreview = dragPreview.scheduleDragPreview

  return { dragToPin, hideDragPreview }
}
