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

export type EdgePickLike = {
  pickEdgeAt: (clientX: number, clientY: number) => { key: string; fromId: string; toId: string } | null
}

export function useCanvasInteractions(opts: {
  isTestMode: () => boolean
  pickNodeAt: (clientX: number, clientY: number) => { id: string } | null
  pickEdgeAt?: (clientX: number, clientY: number) => { key: string; fromId: string; toId: string } | null
  setSelectedNodeId: (id: string | null) => void
  setNodeCardOpen: (open: boolean) => void
  clearHoveredEdge: () => void

  /** Optional hook: return true when edge click was handled (prevents default clear-selection). */
  onEdgeClick?: (edge: { key: string; fromId: string; toId: string }, ptr: { clientX: number; clientY: number }) => boolean

  dragToPin: DragToPinLike
  cameraSystem: CameraSystemLike
  edgeHover: EdgeHoverLike
  getPanActive: () => boolean
}) {
  // Tracks whether a pan or drag-to-pin interaction just completed.
  // Used to suppress the subsequent browser `click` event that fires even after
  // small pointer movements (3-10px). Without this guard, a short pan/drag would
  // clear the user's node selection via onCanvasClick.
  let suppressNextClick = false
  let suppressClickTimer: ReturnType<typeof setTimeout> | null = null

  function markSuppressClick() {
    suppressNextClick = true
    if (suppressClickTimer !== null) clearTimeout(suppressClickTimer)
    // Safety: clear the flag after a short window in case click never fires.
    suppressClickTimer = setTimeout(() => {
      suppressNextClick = false
      suppressClickTimer = null
    }, 300)
  }

  function onCanvasClick(ev: MouseEvent) {
    if (suppressNextClick) {
      suppressNextClick = false
      if (suppressClickTimer !== null) {
        clearTimeout(suppressClickTimer)
        suppressClickTimer = null
      }
      return
    }

    const hit = opts.pickNodeAt(ev.clientX, ev.clientY)
    if (hit) {
      opts.setSelectedNodeId(hit.id)
      // Single click only selects; keep the card closed.
      opts.setNodeCardOpen(false)
      return
    }

    if (opts.pickEdgeAt && opts.onEdgeClick) {
      const edgeHit = opts.pickEdgeAt(ev.clientX, ev.clientY)
      if (edgeHit) {
        const handled = opts.onEdgeClick(edgeHit, { clientX: ev.clientX, clientY: ev.clientY })
        if (handled) return
      }
    }

    opts.setSelectedNodeId(null)
    opts.setNodeCardOpen(false)
  }

  function onCanvasDblClick(ev: MouseEvent) {
    const hit = opts.pickNodeAt(ev.clientX, ev.clientY)
    if (!hit) return

    opts.setSelectedNodeId(hit.id)
    opts.setNodeCardOpen(true)
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
    if (opts.dragToPin.onPointerUp(ev)) {
      // Drag-to-pin consumed this pointer-up. The browser may still fire a `click`
      // event for the same gesture. Suppress it so the drag doesn't accidentally
      // deselect the node the user just pinned.
      markSuppressClick()
      return
    }

    const wasClick = opts.cameraSystem.onPointerUp(ev)
    if (!wasClick) {
      // Camera detected a pan (moved ≥ 3px). The browser may still fire `click`
      // for small movements (3-10px). Suppress it to prevent unintended deselection.
      markSuppressClick()
      return
    }

    // Click on empty background: clear selection.
    opts.setSelectedNodeId(null)
    opts.setNodeCardOpen(false)
    opts.clearHoveredEdge()
  }

  function onCanvasWheel(ev: WheelEvent) {
    // While dragging a node we rely on a camera snapshot for DOM preview; keep camera stable.
    if (opts.dragToPin.dragState.active) return
    opts.cameraSystem.onWheel(ev)
  }

  return {
    onCanvasClick,
    onCanvasDblClick,
    onCanvasPointerDown,
    onCanvasPointerMove,
    onCanvasPointerUp,
    onCanvasWheel,
  }
}
