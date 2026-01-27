import { computed } from 'vue'

import type { LayoutNode } from '../types/layout'

export type PinnedPosMap = Map<string, { x: number; y: number }>
export type BaselinePosMap = Map<string, { x: number; y: number }>

export function usePinning(opts: {
  pinnedPos: PinnedPosMap
  baselineLayoutPos: BaselinePosMap
  getSelectedNodeId: () => string | null
  getLayoutNodeById: (id: string) => LayoutNode | null

  physics: {
    pin: (id: string, x: number, y: number) => void
    unpin: (id: string) => void
    syncFromLayout: () => void
    reheat: (alpha?: number) => void
  }
}) {
  const { pinnedPos, baselineLayoutPos, getSelectedNodeId, getLayoutNodeById, physics } = opts

  const isSelectedPinned = computed(() => {
    const id = getSelectedNodeId()
    if (!id) return false
    return pinnedPos.has(id)
  })

  function captureBaseline(nodes: readonly LayoutNode[]) {
    baselineLayoutPos.clear()
    for (const n of nodes) baselineLayoutPos.set(n.id, { x: n.__x, y: n.__y })
  }

  function reapplyPinnedToLayout() {
    for (const [id, p] of pinnedPos) {
      const n = getLayoutNodeById(id)
      if (!n) continue
      n.__x = p.x
      n.__y = p.y
    }
  }

  function pinSelectedNode() {
    const id = getSelectedNodeId()
    if (!id) return

    const ln = getLayoutNodeById(id)
    if (!ln) return

    pinnedPos.set(id, { x: ln.__x, y: ln.__y })
    physics.pin(id, ln.__x, ln.__y)
    physics.reheat()
  }

  function unpinSelectedNode() {
    const id = getSelectedNodeId()
    if (!id) return

    pinnedPos.delete(id)
    physics.unpin(id)

    const base = baselineLayoutPos.get(id)
    const ln = getLayoutNodeById(id)
    if (ln && base) {
      ln.__x = base.x
      ln.__y = base.y
    }

    physics.syncFromLayout()
    physics.reheat()
  }

  return {
    isSelectedPinned,
    captureBaseline,
    reapplyPinnedToLayout,
    pinSelectedNode,
    unpinSelectedNode,
  }
}
