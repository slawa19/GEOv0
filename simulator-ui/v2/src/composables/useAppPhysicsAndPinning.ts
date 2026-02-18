import { createPhysicsManager } from './usePhysicsManager'
import { usePinning } from './usePinning'

import type { LayoutLink, LayoutNode } from '../types/layout'

export function useAppPhysicsAndPinning(deps: {
  // Physics
  isEnabled: () => boolean
  getLayoutNodes: () => LayoutNode[]
  getLayoutLinks: () => LayoutLink[]
  getQuality: () => 'low' | 'med' | 'high'
  getPinnedPos: () => Map<string, { x: number; y: number }>
  wakeUp?: () => void

  // Pinning state
  pinnedPos: Map<string, { x: number; y: number }>
  baselineLayoutPos: Map<string, { x: number; y: number }>
  getSelectedNodeId: () => string | null
  getLayoutNodeById: (id: string) => LayoutNode | null
}) {
  const physics = createPhysicsManager({
    // Keep e2e + deterministic screenshots stable.
    isEnabled: deps.isEnabled,
    getLayoutNodes: deps.getLayoutNodes,
    getLayoutLinks: deps.getLayoutLinks,
    getQuality: deps.getQuality,
    getPinnedPos: deps.getPinnedPos,
    wakeUp: deps.wakeUp,
  })

  const pinning = usePinning({
    pinnedPos: deps.pinnedPos,
    baselineLayoutPos: deps.baselineLayoutPos,
    getSelectedNodeId: deps.getSelectedNodeId,
    getLayoutNodeById: (id) => deps.getLayoutNodeById(id),
    wakeUp: deps.wakeUp,
    physics: {
      pin: (id, x, y) => physics.pin(id, x, y),
      unpin: (id) => physics.unpin(id),
      syncFromLayout: () => physics.syncFromLayout(),
    },
  })

  return {
    physics,
    pinning,
    isSelectedPinned: pinning.isSelectedPinned,
    pinSelectedNode: pinning.pinSelectedNode,
    unpinSelectedNode: pinning.unpinSelectedNode,
  }
}
