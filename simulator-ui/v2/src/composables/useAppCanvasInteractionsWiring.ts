import { useCanvasInteractions, type CameraSystemLike, type DragToPinLike, type EdgeHoverLike } from './useCanvasInteractions'

export type WakeUpSource = 'user' | 'animation'

export function useAppCanvasInteractionsWiring(opts: {
  isTestMode: () => boolean

  // Wake up render loop on any user interaction that may affect visible state.
  wakeUp?: (source?: WakeUpSource) => void

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

  const wakeUp = (source?: WakeUpSource) => opts.wakeUp?.(source)

  return {
    onCanvasClick: (ev: MouseEvent) => {
      // Click is an instant action; wake up so selection changes render immediately.
      wakeUp('user')
      canvasInteractions.onCanvasClick(ev)
    },
    onCanvasDblClick: (ev: MouseEvent) => {
      // Dblclick is an instant action; wake up so node-card changes render immediately.
      wakeUp('user')
      canvasInteractions.onCanvasDblClick(ev)
    },
    onCanvasPointerDown: (ev: PointerEvent) => {
      // Pointerdown starts an interaction; wake up so capture/drag state is reflected.
      wakeUp('user')
      canvasInteractions.onCanvasPointerDown(ev)
    },
    onCanvasPointerMove: (ev: PointerEvent) => {
      wakeUp('user')
      canvasInteractions.onCanvasPointerMove(ev)
    },
    onCanvasPointerUp: (ev: PointerEvent) => {
      // Pointerup ends an interaction; wake up so final state is rendered promptly.
      wakeUp('user')
      canvasInteractions.onCanvasPointerUp(ev)
    },
    onCanvasWheel: (ev: WheelEvent) => {
      // IMPORTANT:
      // Wheel zoom is applied inside RAF (see useCamera). Calling wakeUp() *before*
      // the camera is actually updated can cause an extra frame rendered with the
      // old camera state. For wheel, we wake up via camera.onCameraChanged.
      canvasInteractions.onCanvasWheel(ev)
    },
  }
}
