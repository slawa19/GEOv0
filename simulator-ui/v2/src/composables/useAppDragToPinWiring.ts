import type { Ref } from 'vue'

import { fillForNode, sizeForNode } from '../render/nodePainter'
import type { GraphNode } from '../types'
import type { LayoutNode } from '../types/layout'
import { VIZ_MAPPING } from '../vizMapping'

import { useAppDragToPinAndPreview } from './useAppDragToPinAndPreview'

type PhysicsLike = {
  pin: (id: string, x: number, y: number) => void
  reheat: (alpha?: number) => void
}

type PinnedPosLike = Map<string, { x: number; y: number }>

type CameraLike = {
  panX: number
  panY: number
  zoom: number
}

export function useAppDragToPinWiring(opts: {
  dragPreviewEl: Ref<HTMLDivElement | null>
  isTestMode: () => boolean

  pickNodeAt: (clientX: number, clientY: number) => { id: string } | null
  getLayoutNodeById: (id: string) => LayoutNode | null
  setSelectedNodeId: (id: string | null) => void
  clearHoveredEdge: () => void

  clientToScreen: (clientX: number, clientY: number) => { x: number; y: number }
  screenToWorld: (x: number, y: number) => { x: number; y: number }
  canvasEl: Ref<HTMLCanvasElement | null>

  renderOnce: () => void

  physics: PhysicsLike
  pinnedPos: PinnedPosLike

  getNodeById: (id: string) => GraphNode | null
  getCamera: () => CameraLike
}) {
  const dragToPinAndPreview = useAppDragToPinAndPreview({
    dragPreviewEl: opts.dragPreviewEl,
    isTestMode: opts.isTestMode,

    pickNodeAt: opts.pickNodeAt,
    getLayoutNodeById: opts.getLayoutNodeById,
    setSelectedNodeId: opts.setSelectedNodeId,
    clearHoveredEdge: opts.clearHoveredEdge,

    clientToScreen: opts.clientToScreen,
    screenToWorld: opts.screenToWorld,
    getCanvasEl: () => opts.canvasEl.value,

    renderOnce: opts.renderOnce,

    pinNodeLive: (id, x, y) => opts.physics.pin(id, x, y),
    commitPinnedPos: (id, x, y) => opts.pinnedPos.set(id, { x, y }),
    reheatPhysics: (alpha) => opts.physics.reheat(alpha),

    getNodeById: opts.getNodeById,
    getCamera: opts.getCamera,
    sizeForNode: (n) => sizeForNode(n),
    fillForNode: (n) => fillForNode(n, VIZ_MAPPING),
  })

  return {
    dragToPin: dragToPinAndPreview.dragToPin,
    hideDragPreview: dragToPinAndPreview.hideDragPreview,
  }
}
