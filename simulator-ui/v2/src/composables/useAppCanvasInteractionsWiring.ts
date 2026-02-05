import { useCanvasInteractions, type CameraSystemLike, type DragToPinLike, type EdgeHoverLike } from './useCanvasInteractions'

export function useAppCanvasInteractionsWiring(opts: {
  isTestMode: () => boolean

  // Wake up render loop on any user interaction that may affect visible state.
  wakeUp?: () => void

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

  const wakeUp = () => opts.wakeUp?.()

  return {
    onCanvasClick: (ev: MouseEvent) => {
      wakeUp()
      canvasInteractions.onCanvasClick(ev)
    },
    onCanvasDblClick: (ev: MouseEvent) => {
      wakeUp()
      canvasInteractions.onCanvasDblClick(ev)
    },
    onCanvasPointerDown: (ev: PointerEvent) => {
      wakeUp()
      canvasInteractions.onCanvasPointerDown(ev)
    },
    onCanvasPointerMove: (ev: PointerEvent) => {
      wakeUp()
      canvasInteractions.onCanvasPointerMove(ev)
    },
    onCanvasPointerUp: (ev: PointerEvent) => {
      wakeUp()
      canvasInteractions.onCanvasPointerUp(ev)
    },
    onCanvasWheel: (ev: WheelEvent) => {
      wakeUp()
      canvasInteractions.onCanvasWheel(ev)
    },
  }
}
