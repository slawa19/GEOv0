import { computed, type ComputedRef } from 'vue'
import type { GraphSnapshot } from '../types'
import { asFiniteNumber, formatAmount2 } from '../utils/numberFormat'

type SelectedNodeEdgeStats = {
  inLimitText: string
  outLimitText: string
  degree: number
}

type UseSelectedNodeEdgeStatsDeps = {
  getSnapshot: () => GraphSnapshot | null
  getSelectedNodeId: () => string | null
}

type UseSelectedNodeEdgeStatsReturn = {
  selectedNodeEdgeStats: ComputedRef<SelectedNodeEdgeStats | null>
}

export function useSelectedNodeEdgeStats(deps: UseSelectedNodeEdgeStatsDeps): UseSelectedNodeEdgeStatsReturn {
  const selectedNodeEdgeStats = computed(() => {
    const snapshot = deps.getSnapshot()
    const id = deps.getSelectedNodeId()
    if (!snapshot || !id) return null

    let inLimit = 0
    let outLimit = 0
    let degree = 0

    for (const l of snapshot.links) {
      const limit = asFiniteNumber(l.trust_limit)
      if (l.source === id) {
        outLimit += limit
        degree += 1
        continue
      }
      if (l.target === id) {
        inLimit += limit
        degree += 1
      }
    }

    return {
      inLimitText: formatAmount2(inLimit),
      outLimitText: formatAmount2(outLimit),
      degree,
    }
  })

  return { selectedNodeEdgeStats }
}
