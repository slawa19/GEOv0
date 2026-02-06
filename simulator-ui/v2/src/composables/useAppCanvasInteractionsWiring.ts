import { useCanvasInteractions, type CameraSystemLike, type DragToPinLike, type EdgeHoverLike } from './useCanvasInteractions'
import type { MarkInteractionOpts } from './interactionHold'

export type WakeUpSource = 'user' | 'animation'

export function useAppCanvasInteractionsWiring(opts: {
  isTestMode: () => boolean

  // Wake up render loop on any user interaction that may affect visible state.
  wakeUp?: (source?: WakeUpSource) => void

  // Optional: mark interaction for Interaction Quality hold window.
  markInteraction?: (opts?: MarkInteractionOpts) => void

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
  const mark = (markOpts?: MarkInteractionOpts) => opts.markInteraction?.(markOpts)

  return {
    onCanvasClick: (ev: MouseEvent) => {
      // No mark(): click is an instant action — no need to reduce render quality.
      wakeUp('user')
      canvasInteractions.onCanvasClick(ev)
    },
    onCanvasDblClick: (ev: MouseEvent) => {
      // No mark(): dblclick is an instant action — no need to reduce render quality.
      wakeUp('user')
      canvasInteractions.onCanvasDblClick(ev)
    },
    onCanvasPointerDown: (ev: PointerEvent) => {
      // No mark(): pointerdown is the start — not yet a continuous interaction.
      wakeUp('user')
      canvasInteractions.onCanvasPointerDown(ev)
    },
    onCanvasPointerMove: (ev: PointerEvent) => {
      // mark() ONLY if a button is pressed (active drag/pan).
      // Hover (buttons === 0) must NOT trigger quality reduction — root cause of the bug.
      if (ev.buttons !== 0) mark()
      wakeUp('user')
      canvasInteractions.onCanvasPointerMove(ev)
    },
    onCanvasPointerUp: (ev: PointerEvent) => {
      // No mark(): pointerup ends the interaction — hold timer handles the tail.
      wakeUp('user')
      canvasInteractions.onCanvasPointerUp(ev)
    },
    onCanvasWheel: (ev: WheelEvent) => {
      mark({ instant: true })
      // IMPORTANT:
      // Wheel zoom is applied inside RAF (see useCamera). Calling wakeUp() *before*
      // the camera is actually updated can cause an extra frame rendered with the
      // old camera state. For wheel, we wake up via camera.onCameraChanged.
      canvasInteractions.onCanvasWheel(ev)
    },
  }
}
