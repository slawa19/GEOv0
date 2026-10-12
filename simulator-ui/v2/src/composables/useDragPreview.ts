import type { Ref } from 'vue'
import type { GraphNode } from '../types'
import type { LayoutNode } from '../types/layout'
import { getNodeShape } from '../types/nodeShape'
import type { NodeLook } from '../vizMapping'

/** The hub ring of the preview: its line and its distance from the outline, in CSS px (as on the canvas, `nodePainter.ts`). */
const HUB_RING_LINE_PX = 1.4
const HUB_RING_GAP_PX = 3.5

/**
 * The DOM element that stands for a node while it is dragged (the canvas does not draw that node then).
 *
 * 039 slice B: it carries what the node carries - the role colour, the sign of the net position, the dashed outline
 * of a freeze, the ring of a hub. And it is NOT a picture taken when the drag began (external review of 2026-10-12,
 * class 1): `refreshDragPreview` paints it again from the node's CURRENT fields and the CURRENT palette, and the
 * owner of the drag calls it on every frame of the drag (`useAppDragToPinAndPreview.ts` `syncDragWithScene`), so a
 * patch accepted during the drag, or a theme change, shows on the next frame.
 */
export function useDragPreview(opts: {
  el: Ref<HTMLDivElement | null>
  getDraggedNode: () => LayoutNode | null
  getNodeById: (id: string) => GraphNode | null
  getCamera: () => { panX: number; panY: number; zoom: number }
  renderOnce: () => void
  sizeForNode: (n: GraphNode) => { w: number; h: number }
  fillForNode: (n: GraphNode) => string
  lookForNode: (n: GraphNode) => NodeLook
  /** The resolved colour of the hub ring - read at every paint, never kept. */
  hubColor: () => string
}) {
  const { el, getDraggedNode, getNodeById, getCamera, renderOnce, sizeForNode, fillForNode, lookForNode, hubColor } = opts

  let previewW = 0
  let previewH = 0
  let pending = false
  let rafId: number | null = null
  /** The node the preview stands for, by id: the node OBJECT is looked up again at every paint. */
  let shownNodeId: string | null = null
  /** What was last written to the element; styles are written only when this changes. */
  let paintedKey = ''

  function hideDragPreview() {
    shownNodeId = null
    paintedKey = ''
    const node = el.value
    if (node) node.style.display = 'none'
    if (rafId !== null) {
      window.cancelAnimationFrame(rafId)
      rafId = null
      pending = false
    }
  }

  /** Paint the element from the node as it is NOW. */
  function paint(node: HTMLDivElement, n: GraphNode) {
    const s = sizeForNode(n)
    const fill = fillForNode(n)
    const look = lookForNode(n)
    const shapeKey = getNodeShape(n) ?? 'circle'
    const ring = look.hub ? hubColor() : ''
    const key = `${fill}|${look.sign}|${look.frozen ? 1 : 0}|${ring}|${shapeKey}|${s.w}x${s.h}`
    if (key === paintedKey) return
    paintedKey = key

    previewW = s.w
    previewH = s.h

    // While a node is dragged the canvas does not draw it: this element stands for it, and it carries the same
    // non-colour cues - the sign of the net position, the dashed outline of a freeze, the ring of a hub.
    node.textContent = look.sign
    node.style.borderStyle = look.frozen ? 'dashed' : 'solid'
    node.style.outline = ring ? `${HUB_RING_LINE_PX}px solid ${ring}` : 'none'
    node.style.outlineOffset = ring ? `${HUB_RING_GAP_PX}px` : '0px'

    node.style.width = `${previewW}px`
    node.style.height = `${previewH}px`
    node.style.background = `linear-gradient(135deg, ${fill}88, ${fill}44)`
    node.style.borderColor = `${fill}aa`
    node.style.boxShadow = `0 0 0 1px rgba(255,255,255,0.18), 0 12px 30px rgba(0,0,0,0.45), 0 0 24px ${fill}55`
    node.style.borderRadius = shapeKey === 'rounded-rect' ? '5px' : '999px'
  }

  function showDragPreviewForNode(nodeId: string) {
    const node = el.value
    if (!node) return

    const n = getNodeById(nodeId)
    if (!n) return

    shownNodeId = nodeId
    paintedKey = ''
    paint(node, n)
    node.style.display = 'flex'
  }

  /**
   * Paint the shown preview again from the node's current fields and the current palette. `false`: the node the
   * preview stands for is no longer in the scene - the caller must end the drag; nothing is painted.
   */
  function refreshDragPreview(): boolean {
    if (shownNodeId === null) return true
    const node = el.value
    if (!node) return true
    const n = getNodeById(shownNodeId)
    if (!n) return false
    paint(node, n)
    return true
  }

  function scheduleDragPreview() {
    const node = el.value
    if (!node) return

    const n = getDraggedNode()
    if (!n) return

    if (pending) return
    pending = true

    rafId = window.requestAnimationFrame(() => {
      pending = false
      rafId = null

      // Snapshot camera once per frame for stable DOM preview.
      const cam = getCamera()
      const p = { x: n.__x * cam.zoom + cam.panX, y: n.__y * cam.zoom + cam.panY }

      const x = p.x - previewW / 2
      const y = p.y - previewH / 2
      node.style.transform = `translate3d(${x}px, ${y}px, 0)`

      // Edges are drawn in world-space on the main canvas.
      renderOnce()
    })
  }

  return { hideDragPreview, showDragPreviewForNode, refreshDragPreview, scheduleDragPreview }
}
