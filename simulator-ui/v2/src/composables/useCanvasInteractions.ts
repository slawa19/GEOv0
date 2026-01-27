export type CanvasPointerLike = {
  clientX: number
  clientY: number
}

export type CameraSystemLike = {
  onPointerDown: (ev: PointerEvent) => void
  onPointerMove: (ev: PointerEvent) => void
  onPointerUp: (ev: PointerEvent) => boolean
  onWheel: (ev: WheelEvent) => void
}

export type DragToPinLike = {
  dragState: { active: boolean }
  onPointerDown: (ev: PointerEvent) => boolean
  onPointerMove: (ev: PointerEvent) => boolean
  onPointerUp: (ev: PointerEvent) => boolean
}

export type EdgeHoverLike = {
  onPointerMove: (ev: PointerEvent, ctx: { panActive: boolean }) => void
}

export function useCanvasInteractions(opts: {
  isTestMode: () => boolean
  pickNodeAt: (clientX: number, clientY: number) => { id: string } | null
  setSelectedNodeId: (id: string | null) => void
  clearHoveredEdge: () => void
  dragToPin: DragToPinLike
  cameraSystem: CameraSystemLike
  edgeHover: EdgeHoverLike
  getPanActive: () => boolean
}) {
  function onCanvasClick(ev: MouseEvent) {
    const hit = opts.pickNodeAt(ev.clientX, ev.clientY)
    if (!hit) {
      opts.setSelectedNodeId(null)
      return
    }
    opts.setSelectedNodeId(hit.id)
  }

  function onCanvasPointerDown(ev: PointerEvent) {
    // Keep tests deterministic.
    if (opts.isTestMode()) return

    // If user grabs a node: select it, don't pan.
    // Also allow optional drag-to-pin interaction.
    if (opts.dragToPin.onPointerDown(ev)) return

    opts.cameraSystem.onPointerDown(ev)
  }

  function onCanvasPointerMove(ev: PointerEvent) {
    if (opts.dragToPin.onPointerMove(ev)) return

    const panActive = opts.getPanActive()
    opts.edgeHover.onPointerMove(ev, { panActive })

    if (!panActive) return

    opts.cameraSystem.onPointerMove(ev)
  }

  function onCanvasPointerUp(ev: PointerEvent) {
    if (opts.dragToPin.onPointerUp(ev)) return

    const wasClick = opts.cameraSystem.onPointerUp(ev)
    if (!wasClick) return

    // Click on empty background: clear selection.
    opts.setSelectedNodeId(null)
    opts.clearHoveredEdge()
  }

  function onCanvasWheel(ev: WheelEvent) {
    // While dragging a node we rely on a camera snapshot for DOM preview; keep camera stable.
    if (opts.dragToPin.dragState.active) return
    opts.cameraSystem.onWheel(ev)
  }

  return {
    onCanvasClick,
    onCanvasPointerDown,
    onCanvasPointerMove,
    onCanvasPointerUp,
    onCanvasWheel,
  }
}
