import type { ComputedRef, Ref } from 'vue'
import { computed } from 'vue'
import type { GraphNode } from '../types'
import type { Point, LayoutNodeWithId } from '../types/layout'


type UseNodeCardDeps = {
  hostEl: Ref<HTMLElement | null>
  selectedNodeId: Ref<string | null>
  getNodeById: (id: string | null) => GraphNode | null
  getLayoutNodeById: (id: string) => LayoutNodeWithId | null
  worldToScreen: (x: number, y: number) => { x: number; y: number }
}

type UseNodeCardReturn = {
  selectedNode: ComputedRef<GraphNode | null>
  /** Screen-space center of the selected node (host-relative). null when no node selected. */
  selectedNodeScreenCenter: ComputedRef<Point | null>
}

export function useNodeCard(deps: UseNodeCardDeps): UseNodeCardReturn {
  const selectedNode = computed(() => deps.getNodeById(deps.selectedNodeId.value))

  /** Host-relative screen center of the selected node. */
  const selectedNodeScreenCenter = computed<Point | null>(() => {
    if (!deps.hostEl.value || !selectedNode.value) return null
    const node = deps.getLayoutNodeById(selectedNode.value.id)
    if (!node) return null
    const p = deps.worldToScreen(node.__x, node.__y)
    return { x: p.x, y: p.y }
  })

  return { selectedNode, selectedNodeScreenCenter }
}
