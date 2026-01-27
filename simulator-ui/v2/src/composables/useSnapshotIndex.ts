import { computed } from 'vue'
import type { GraphNode, GraphSnapshot } from '../types'

export function useSnapshotIndex(opts: { getSnapshot: () => GraphSnapshot | null }) {
  const snapshot = computed(() => opts.getSnapshot())

  const nodeById = computed(() => {
    const s = snapshot.value
    if (!s) return new Map<string, GraphNode>()
    return new Map(s.nodes.map((n) => [n.id, n] as const))
  })

  function getNodeById(id: string | null): GraphNode | null {
    if (!id) return null
    return nodeById.value.get(id) ?? null
  }

  return {
    nodeById,
    getNodeById,
  }
}
