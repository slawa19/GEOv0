import { computed, type ComputedRef } from 'vue'
import type { GraphNode, GraphSnapshot } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'
import type { LabelsLod } from '../types/uiPrefs'

export type LabelNode = { id: string; x: number; y: number; text: string; color: string }

type UseLabelNodesDeps = {
  isTestMode: () => boolean
  getLabelsLod: () => LabelsLod
  getSnapshot: () => GraphSnapshot | null
  getSelectedNodeId: () => string | null

  getLayoutLinks: () => LayoutLink[]
  getLayoutNodeById: (id: string) => LayoutNode | null

  getNodeById: (id: string) => GraphNode | null
  getCameraZoom: () => number
  sizeForNode: (n: LayoutNode) => { w: number; h: number }
  fxColorForNode: (nodeId: string, fallback: string) => string
}

type UseLabelNodesReturn = {
  labelNodes: ComputedRef<LabelNode[]>
}

export function useLabelNodes(deps: UseLabelNodesDeps): UseLabelNodesReturn {
  const labelNodes = computed<LabelNode[]>(() => {
    if (deps.isTestMode()) return []
    const labelsLod = deps.getLabelsLod()
    if (labelsLod === 'off') return []

    const snapshot = deps.getSnapshot()
    const selectedId = deps.getSelectedNodeId()
    if (!snapshot || !selectedId) return []

    const ids = new Set<string>()
    ids.add(selectedId)

    if (labelsLod === 'neighbors') {
      const center = selectedId
      for (const l of deps.getLayoutLinks()) {
        if (l.source === center) ids.add(l.target)
        else if (l.target === center) ids.add(l.source)
      }
    }

    const max = 28
    const out: LabelNode[] = []

    for (const id of Array.from(ids)) {
      if (out.length >= max) break

      const ln = deps.getLayoutNodeById(id)
      if (!ln) continue
      const gn = deps.getNodeById(id)

      // Place the label below the node by a constant screen-space offset.
      const z = Math.max(0.01, deps.getCameraZoom())
      const sz = deps.sizeForNode(ln)
      const dyPx = Math.max(sz.w, sz.h) / 2 + 14
      const dyW = dyPx / z

      out.push({
        id,
        x: ln.__x,
        y: ln.__y + dyW,
        text: gn?.name ? String(gn.name) : id,
        color: deps.fxColorForNode(id, '#e2e8f0'),
      })
    }

    return out
  })

  return { labelNodes }
}
