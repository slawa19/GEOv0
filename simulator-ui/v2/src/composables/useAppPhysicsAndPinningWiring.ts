import { reactive } from 'vue'

import type { LayoutLink, LayoutNode } from '../types/layout'
import type { Quality } from '../types/uiPrefs'

import { useAppPhysicsAndPinning } from './useAppPhysicsAndPinning'

export function useAppPhysicsAndPinningWiring(opts: {
  isEnabled: () => boolean
  getLayoutNodes: () => LayoutNode[]
  getLayoutLinks: () => LayoutLink[]
  getQuality: () => Quality
  getSelectedNodeId: () => string | null
  getLayoutNodeById: (id: string) => LayoutNode | null
  wakeUp?: () => void
}) {
  const baselineLayoutPos = new Map<string, { x: number; y: number }>()
  const pinnedPos = reactive(new Map<string, { x: number; y: number }>())

  const physicsAndPinning = useAppPhysicsAndPinning({
    isEnabled: opts.isEnabled,
    getLayoutNodes: opts.getLayoutNodes,
    getLayoutLinks: opts.getLayoutLinks,
    getQuality: opts.getQuality,
    getPinnedPos: () => pinnedPos,
    wakeUp: opts.wakeUp,
    pinnedPos,
    baselineLayoutPos,
    getSelectedNodeId: opts.getSelectedNodeId,
    getLayoutNodeById: opts.getLayoutNodeById,
  })

  return {
    baselineLayoutPos,
    pinnedPos,

    physics: physicsAndPinning.physics,
    pinning: physicsAndPinning.pinning,

    isSelectedPinned: physicsAndPinning.isSelectedPinned,
    pinSelectedNode: physicsAndPinning.pinSelectedNode,
    unpinSelectedNode: physicsAndPinning.unpinSelectedNode,
  }
}
