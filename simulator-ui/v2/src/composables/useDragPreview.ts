import type { Ref } from 'vue'
import type { GraphNode } from '../types'
import type { LayoutNode } from '../types/layout'

export function useDragPreview(opts: {
  el: Ref<HTMLDivElement | null>
  getDraggedNode: () => LayoutNode | null
  getNodeById: (id: string) => GraphNode | null
  getCamera: () => { panX: number; panY: number; zoom: number }
  renderOnce: () => void
  sizeForNode: (n: GraphNode) => { w: number; h: number }
  fillForNode: (n: GraphNode) => string
}) {
  const { el, getDraggedNode, getNodeById, getCamera, renderOnce, sizeForNode, fillForNode } = opts

  let previewW = 0
  let previewH = 0
  let pending = false
  let rafId: number | null = null

  function hideDragPreview() {
    const node = el.value
    if (node) node.style.display = 'none'
    if (rafId !== null) {
      window.cancelAnimationFrame(rafId)
      rafId = null
      pending = false
    }
  }

  function showDragPreviewForNode(nodeId: string) {
    const node = el.value
    if (!node) return

    const n = getNodeById(nodeId)
    if (!n) return

    const s = sizeForNode(n)
    previewW = s.w
    previewH = s.h

    const fill = fillForNode(n)

    node.style.display = 'block'
    node.style.width = `${previewW}px`
    node.style.height = `${previewH}px`
    node.style.background = `linear-gradient(135deg, ${fill}88, ${fill}44)`
    node.style.borderColor = `${fill}aa`
    node.style.boxShadow = `0 0 0 1px rgba(255,255,255,0.18), 0 12px 30px rgba(0,0,0,0.45), 0 0 24px ${fill}55`
    const shapeKey = String((n as any).viz_shape_key ?? 'circle')
    node.style.borderRadius = shapeKey === 'rounded-rect' ? '5px' : '999px'
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

  return { hideDragPreview, showDragPreviewForNode, scheduleDragPreview }
}
