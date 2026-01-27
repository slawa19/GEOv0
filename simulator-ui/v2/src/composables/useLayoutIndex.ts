import { computed, type ComputedRef } from 'vue'
import type { LayoutLink, LayoutNode } from '../types/layout'

type LayoutIndex = {
  nodeById: Map<string, LayoutNode>
  linkByKey: Map<string, LayoutLink>
  incidentEdgeKeysByNodeId: Map<string, Set<string>>
}

type UseLayoutIndexDeps = {
  getLayoutNodes: () => LayoutNode[]
  getLayoutLinks: () => LayoutLink[]
  getSelectedNodeId: () => string | null
}

type UseLayoutIndexReturn = {
  layoutIndex: ComputedRef<LayoutIndex>
  layoutNodeMap: ComputedRef<Map<string, LayoutNode>>
  layoutLinkMap: ComputedRef<Map<string, LayoutLink>>
  selectedIncidentEdgeKeys: ComputedRef<Set<string>>

  getLayoutNodeById: (id: string) => LayoutNode | null
  getLinkByKey: (key: string) => LayoutLink | undefined
}

export function useLayoutIndex(deps: UseLayoutIndexDeps): UseLayoutIndexReturn {
  const layoutIndex = computed<LayoutIndex>(() => {
    const nodeById = new Map<string, LayoutNode>()
    for (const n of deps.getLayoutNodes()) nodeById.set(n.id, n)

    const linkByKey = new Map<string, LayoutLink>()
    const incidentEdgeKeysByNodeId = new Map<string, Set<string>>()

    for (const l of deps.getLayoutLinks()) {
      linkByKey.set(l.__key, l)

      const a = l.source
      const b = l.target

      const sA = incidentEdgeKeysByNodeId.get(a) ?? new Set<string>()
      sA.add(l.__key)
      incidentEdgeKeysByNodeId.set(a, sA)

      const sB = incidentEdgeKeysByNodeId.get(b) ?? new Set<string>()
      sB.add(l.__key)
      incidentEdgeKeysByNodeId.set(b, sB)
    }

    return { nodeById, linkByKey, incidentEdgeKeysByNodeId }
  })

  const layoutNodeMap = computed(() => layoutIndex.value.nodeById)
  const layoutLinkMap = computed(() => layoutIndex.value.linkByKey)

  const selectedIncidentEdgeKeys = computed(() => {
    const id = deps.getSelectedNodeId()
    if (!id) return new Set<string>()
    return layoutIndex.value.incidentEdgeKeysByNodeId.get(id) ?? new Set<string>()
  })

  function getLayoutNodeById(id: string) {
    return layoutIndex.value.nodeById.get(id) ?? null
  }

  function getLinkByKey(key: string) {
    return layoutIndex.value.linkByKey.get(key)
  }

  return { layoutIndex, layoutNodeMap, layoutLinkMap, selectedIncidentEdgeKeys, getLayoutNodeById, getLinkByKey }
}
