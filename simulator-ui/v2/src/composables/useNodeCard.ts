import type { ComputedRef, Ref } from 'vue'
import { computed } from 'vue'
import type { GraphNode } from '../types'

type LayoutNodeLike = { id: string; __x: number; __y: number }

type UseNodeCardDeps = {
  hostEl: Ref<HTMLElement | null>
  selectedNodeId: Ref<string | null>
  getNodeById: (id: string | null) => GraphNode | null
  getLayoutNodeById: (id: string) => LayoutNodeLike | null
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
    const cardH = 170
    const dx = 24

    const xRight = p.x + dx
    const xLeft = p.x - dx - cardW

    let x: number
    if (xRight + cardW <= rect.width - pad) {
      x = xRight
    } else if (xLeft >= pad) {
      x = xLeft
    } else {
      x = clamp(xRight, pad, rect.width - pad - cardW)
    }

    const y = clamp(p.y - cardH / 2, pad, rect.height - pad - cardH)
    return { left: `${x}px`, top: `${y}px` }
  }

  return { selectedNode, nodeCardStyle }
}
