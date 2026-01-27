import { useCanvasInteractions, type CameraSystemLike, type DragToPinLike, type EdgeHoverLike } from './useCanvasInteractions'

export function useAppCanvasInteractionsWiring(opts: {
  isTestMode: () => boolean

  pickNodeAt: (clientX: number, clientY: number) => { id: string } | null
  selectNode: (id: string | null) => void
  setNodeCardOpen: (open: boolean) => void
  clearHoveredEdge: () => void

  dragToPin: DragToPinLike
  cameraSystem: CameraSystemLike
  edgeHover: EdgeHoverLike
  getPanActive: () => boolean
}) {
  const canvasInteractions = useCanvasInteractions({
    isTestMode: opts.isTestMode,
    pickNodeAt: opts.pickNodeAt,
    setSelectedNodeId: opts.selectNode,
    setNodeCardOpen: opts.setNodeCardOpen,
    clearHoveredEdge: opts.clearHoveredEdge,
    dragToPin: opts.dragToPin,
    cameraSystem: opts.cameraSystem,
    edgeHover: opts.edgeHover,
    getPanActive: opts.getPanActive,
  })

  return {
    onCanvasClick: canvasInteractions.onCanvasClick,
    onCanvasDblClick: canvasInteractions.onCanvasDblClick,
    onCanvasPointerDown: canvasInteractions.onCanvasPointerDown,
    onCanvasPointerMove: canvasInteractions.onCanvasPointerMove,
    onCanvasPointerUp: canvasInteractions.onCanvasPointerUp,
    onCanvasWheel: canvasInteractions.onCanvasWheel,
  }
}
