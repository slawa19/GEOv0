import type { ComputedRef, Ref } from 'vue'
import { computed } from 'vue'
import type { GraphNode } from '../types'

type LayoutNodeLike = { id: string; __x: number; __y: number }

type UseNodeCardDeps = {
  hostEl: Ref<HTMLElement | null>
  selectedNodeId: Ref<string | null>
  getNodeById: (id: string | null) => GraphNode | null
  getLayoutNodeById: (id: string) => LayoutNodeLike | null
  getNodeScreenSize: (node: GraphNode) => { w: number; h: number }
  worldToScreen: (x: number, y: number) => { x: number; y: number }
}

type UseNodeCardReturn = {
  selectedNode: ComputedRef<GraphNode | null>
  nodeCardStyle: () => { left?: string; top?: string; display?: string }
}

export function useNodeCard(deps: UseNodeCardDeps): UseNodeCardReturn {
  const selectedNode = computed(() => deps.getNodeById(deps.selectedNodeId.value))

  function clamp(v: number, lo: number, hi: number) {
    return Math.max(lo, Math.min(hi, v))
  }

  function nodeCardStyle() {
    const host = deps.hostEl.value
    if (!host || !selectedNode.value) return { display: 'none' }

    const node = deps.getLayoutNodeById(selectedNode.value.id)
    if (!node) return { display: 'none' }

    const rect = host.getBoundingClientRect()
    const p = deps.worldToScreen(node.__x, node.__y)

    const pad = 12
    const cardW = 260
    const cardH = 146
    const gap = 18

    const { w: nw, h: nh } = deps.getNodeScreenSize(selectedNode.value)
    const nodeW = Math.max(10, nw) + 18
    const nodeH = Math.max(10, nh) + 18

    const nodeRect = {
      x: p.x - nodeW / 2,
      y: p.y - nodeH / 2,
      w: nodeW,
      h: nodeH,
    }

    const clampCard = (x: number, y: number) => ({
      x: clamp(x, pad, rect.width - pad - cardW),
      y: clamp(y, pad, rect.height - pad - cardH),
    })

    const intersects = (a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }) =>
      !(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y)

    const candidates = [
      // right
      { x: p.x + nodeW / 2 + gap, y: p.y - cardH / 2 },
      // left
      { x: p.x - nodeW / 2 - gap - cardW, y: p.y - cardH / 2 },
      // bottom
      { x: p.x - cardW / 2, y: p.y + nodeH / 2 + gap },
      // top
      { x: p.x - cardW / 2, y: p.y - nodeH / 2 - gap - cardH },
    ]

    for (const c of candidates) {
      const t = clampCard(c.x, c.y)
      const cardRect = { x: t.x, y: t.y, w: cardW, h: cardH }
      if (!intersects(cardRect, nodeRect)) {
        return { left: `${t.x}px`, top: `${t.y}px` }
      }
    }

    // Fallback: keep card in-bounds.
    const t = clampCard(p.x + nodeW / 2 + gap, p.y - cardH / 2)
    return { left: `${t.x}px`, top: `${t.y}px` }
  }

  return { selectedNode, nodeCardStyle }
}
