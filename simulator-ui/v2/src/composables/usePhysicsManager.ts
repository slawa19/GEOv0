import { createDefaultConfig, createPhysicsEngine, type PhysicsEngine } from '../layout/physicsD3'
import type { LayoutLink, LayoutNode } from '../types/layout'

export type PhysicsQuality = 'low' | 'med' | 'high'

export function createPhysicsManager(opts: {
  isEnabled: () => boolean
  getLayoutNodes: () => LayoutNode[]
  getLayoutLinks: () => LayoutLink[]
  getQuality: () => PhysicsQuality
  getPinnedPos: () => Iterable<[string, { x: number; y: number }]>
}) {
  const { isEnabled, getLayoutNodes, getLayoutLinks, getQuality, getPinnedPos } = opts

  let engine: PhysicsEngine | null = null

  function stop() {
    engine?.stop()
    engine = null
  }

  function recreateForCurrentLayout(viewport: { w: number; h: number }) {
    // Layout arrays are replaced on every relayout, so the physics engine must be recreated.
    // Always tear down the previous engine first to avoid leaks / double-ticking.
    stop()

    if (!isEnabled()) return

    const nodes = getLayoutNodes()
    const links = getLayoutLinks()

    if (!nodes || nodes.length === 0) return

    const config = createDefaultConfig({
      width: viewport.w,
      height: viewport.h,
      nodeCount: nodes.length,
      quality: getQuality(),
    })

    engine = createPhysicsEngine({ nodes, links, config })

    // Re-apply pinned nodes as fixed constraints for the simulation.
    for (const [id, p] of getPinnedPos()) engine.pin(id, p.x, p.y)

    engine.reheat()
  }

  function updateViewport(w: number, h: number, reheatAlpha = 0.15) {
    if (!engine) return
    if (!isEnabled()) return
    engine.updateViewport(w, h)
    engine.reheat(reheatAlpha)
  }

  function tickAndSyncToLayout() {
    if (!engine) return
    engine.tick()
    engine.syncToLayout()
  }

  function pin(id: string, x: number, y: number) {
    engine?.pin(id, x, y)
  }

  function unpin(id: string) {
    engine?.unpin(id)
  }

  function syncFromLayout() {
    engine?.syncFromLayout()
  }

  function reheat(alpha?: number) {
    engine?.reheat(alpha)
  }

  return {
    stop,
    recreateForCurrentLayout,
    updateViewport,
    tickAndSyncToLayout,
    pin,
    unpin,
    syncFromLayout,
    reheat,
  }
}
