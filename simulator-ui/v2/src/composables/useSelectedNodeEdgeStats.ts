import { computed, type ComputedRef } from 'vue'
import type { GraphSnapshot } from '../types'
import { equivalentPrecision } from '../config/equivalentPrecision'
import { addMoney, formatMoney } from '../utils/money'

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

/**
 * Trust-limit totals for the node card.
 *
 * 012 / `F-012-5`: this used to read every `trust_limit` through `asFiniteNumber` and print
 * the total with `toFixed(2)`. Both halves were wrong. The double lost limits the ledger
 * stores exactly — two `Numeric(20, 8)` values one atom apart collapsed into one rendering
 * — and the digit count was the constant 2 whatever the equivalent declared. The sum is now
 * exact decimal arithmetic on strings, and the digit count comes from the snapshot's own
 * equivalent.
 */
export function computeNodeEdgeStats(snapshot: GraphSnapshot, id: string): SelectedNodeEdgeStats {
  let inLimit = '0'
  let outLimit = '0'
  let degree = 0

  for (const l of snapshot.links) {
    if (l.source === id) {
      outLimit = addMoney(outLimit, l.trust_limit)
      degree += 1
      continue
    }
    if (l.target === id) {
      inLimit = addMoney(inLimit, l.trust_limit)
      degree += 1
    }
  }

  const precision = equivalentPrecision(snapshot.equivalent)

  return {
    inLimitText: formatMoney(inLimit, precision),
    outLimitText: formatMoney(outLimit, precision),
    degree,
  }
}

export function useSelectedNodeEdgeStats(deps: UseSelectedNodeEdgeStatsDeps): UseSelectedNodeEdgeStatsReturn {
  const selectedNodeEdgeStats = computed(() => {
    const snapshot = deps.getSnapshot()
    const id = deps.getSelectedNodeId()
    if (!snapshot || !id) return null

    return computeNodeEdgeStats(snapshot, id)
  })

  return { selectedNodeEdgeStats }
}
