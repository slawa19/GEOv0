import { reactive } from 'vue'
import type { LayoutNode } from '../types/layout'

type NodeHit = { id: string }

export function useDragToPinInteraction(opts: {
  isEnabled: () => boolean
  pickNodeAt: (clientX: number, clientY: number) => NodeHit | null
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

  showDragPreviewForNode: (nodeId: string) => void
  scheduleDragPreview: () => void
  hideDragPreview: () => void
}) {
  const {
    isEnabled,
    pickNodeAt,
    getLayoutNodeById,
    setSelectedNodeId,
    clearHoveredEdge,
    clientToScreen,
    screenToWorld,
    getCanvasEl,
    renderOnce,
    pinNodeLive,
    commitPinnedPos,
    reheatPhysics,
    showDragPreviewForNode,
    scheduleDragPreview,
    hideDragPreview,
  } = opts

  const dragState = reactive({
    active: false,
    dragging: false,
    nodeId: null as string | null,
    pointerId: null as number | null,
    startClientX: 0,
    startClientY: 0,
    offsetX: 0,
    offsetY: 0,
    cachedNode: null as LayoutNode | null,
  })

  function onPointerDown(ev: PointerEvent): boolean {
    if (!isEnabled()) return false

    const hit = pickNodeAt(ev.clientX, ev.clientY)
    if (!hit) return false

    setSelectedNodeId(hit.id)
    clearHoveredEdge()

    const ln = getLayoutNodeById(hit.id)
    const canvas = getCanvasEl()
    if (!ln || !canvas) return true

    dragState.active = true
    dragState.dragging = false
    dragState.nodeId = hit.id
    dragState.pointerId = ev.pointerId
    dragState.startClientX = ev.clientX
    dragState.startClientY = ev.clientY
    dragState.cachedNode = ln

    const s = clientToScreen(ev.clientX, ev.clientY)
    const p = screenToWorld(s.x, s.y)
    dragState.offsetX = ln.__x - p.x
    dragState.offsetY = ln.__y - p.y

    try {
      canvas.setPointerCapture(ev.pointerId)
    } catch {
      // ignore
    }

    return true
  }

  function onPointerMove(ev: PointerEvent): boolean {
    if (!dragState.active || dragState.pointerId !== ev.pointerId || !dragState.cachedNode) return false

    const dx = ev.clientX - dragState.startClientX
    const dy = ev.clientY - dragState.startClientY
    const dist2 = dx * dx + dy * dy
    const threshold2 = 4 * 4

    if (!dragState.dragging && dist2 < threshold2) return true

    const s = clientToScreen(ev.clientX, ev.clientY)
    const p = screenToWorld(s.x, s.y)
    const x = p.x + dragState.offsetX
    const y = p.y + dragState.offsetY

    dragState.cachedNode.__x = x
    dragState.cachedNode.__y = y

    const id = dragState.nodeId
    if (id) pinNodeLive(id, x, y)

    if (!dragState.dragging) {
      dragState.dragging = true
      clearHoveredEdge()

      if (id) showDragPreviewForNode(id)
      scheduleDragPreview()

      // Do NOT reheat physics during drag.
      // Any nonzero alpha causes d3-force to run ~N ticks moving unpinned nodes,
      // producing a visible "graph jump" that accumulates while the user drags.
      // The dragged node is already pinned via pinNodeLive(); connected edges
      // stretch/compress naturally via canvas rendering without physics.

      // Hide the node from canvas and lock in the baseline frame.
      renderOnce()
      return true
    }

    scheduleDragPreview()
    return true
  }

  function onPointerUp(ev: PointerEvent): boolean {
    if (!dragState.active || dragState.pointerId !== ev.pointerId) return false

    const id = dragState.nodeId
    const ln = dragState.cachedNode

    if (dragState.dragging && id && ln) {
      // ALWAYS pin the node after drag (like v1) so user can see its edges
      commitPinnedPos(id, ln.__x, ln.__y)
      pinNodeLive(id, ln.__x, ln.__y)
      // No physics reheat on drop — the drag interaction is purely positional.
      // See comment in onPointerMove for rationale.
    }

    hideDragPreview()

    dragState.active = false
    dragState.dragging = false
    dragState.nodeId = null
    dragState.pointerId = null
    dragState.cachedNode = null

    const canvas = getCanvasEl()
    try {
      canvas?.releasePointerCapture(ev.pointerId)
    } catch {
      // ignore
    }

    renderOnce()
    return true
  }

  return { dragState, onPointerDown, onPointerMove, onPointerUp }
}
