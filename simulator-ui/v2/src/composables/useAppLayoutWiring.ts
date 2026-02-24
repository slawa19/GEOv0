import type { ComputedRef, Ref } from 'vue'

import type { LayoutMode } from '../layout/forceLayout'
import type { GraphSnapshot } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'

import { createAppComputeLayout } from './useAppComputeLayout'
import { useLayoutCoordinator } from './useLayoutCoordinator'

type PinningLike = {
  captureBaseline: (nodes: LayoutNode[]) => void
  reapplyPinnedToLayout: () => void
}

type PhysicsLike = {
  recreateForCurrentLayout: (opts: { w: number; h: number }) => void
}

export function useAppLayoutWiring(opts: {
  canvasEl: Ref<HTMLCanvasElement | null>
  fxCanvasEl: Ref<HTMLCanvasElement | null>
  hostEl: Ref<HTMLDivElement | null>

  snapshot: ComputedRef<GraphSnapshot | null>
  layoutMode: Ref<LayoutMode>

  dprClamp: ComputedRef<number>
  isTestMode: ComputedRef<boolean>

  getSourcePath: () => string

  computeLayoutForMode: typeof import('../layout/forceLayout').computeLayoutForMode

  // Optional: notify render loop that layout/viewport changed.
  wakeUp?: () => void
}) {
  let computeLayoutImpl: ((snapshot: GraphSnapshot, w: number, h: number, mode: LayoutMode) => void) | null = null
  let pendingComputeLayout:
    | { snapshot: GraphSnapshot; w: number; h: number; mode: LayoutMode }
    | null = null

  function computeLayout(snapshot: GraphSnapshot, w: number, h: number, mode: LayoutMode) {
    if (!computeLayoutImpl) {
      // Graceful behavior: layout coordinator may trigger compute before wiring is fully initialized.
      // Buffer the latest request and replay it once `initComputeLayout()` is called.
      pendingComputeLayout = { snapshot, w, h, mode }
      return
    }
    computeLayoutImpl(snapshot, w, h, mode)
  }

  const layoutCoordinator = useLayoutCoordinator<LayoutNode, LayoutLink, LayoutMode, GraphSnapshot>({
    canvasEl: opts.canvasEl,
    fxCanvasEl: opts.fxCanvasEl,
    hostEl: opts.hostEl,
    snapshot: opts.snapshot,
    layoutMode: opts.layoutMode,
    dprClamp: opts.dprClamp,
    isTestMode: opts.isTestMode,
    getSourcePath: opts.getSourcePath,
    computeLayout,
    wakeUp: opts.wakeUp,
  })

  function initComputeLayout(deps: { pinning: PinningLike; physics: PhysicsLike }) {
    computeLayoutImpl = createAppComputeLayout<GraphSnapshot, LayoutMode, LayoutNode, LayoutLink>({
      isTestMode: () => opts.isTestMode.value,
      computeLayoutForMode: opts.computeLayoutForMode,
      setLayout: (nodes, links) => {
        // Critical: keep layout.nodes/links raw (non-reactive) for physics tick performance.
        layoutCoordinator.setLayout(nodes, links)
      },
      onAfterLayout: (result, ctx) => {
        deps.pinning.captureBaseline(result.nodes)
        deps.pinning.reapplyPinnedToLayout()
        deps.physics.recreateForCurrentLayout({ w: ctx.w, h: ctx.h })
      },
    })

    if (pendingComputeLayout) {
      const pending = pendingComputeLayout
      pendingComputeLayout = null
      computeLayoutImpl(pending.snapshot, pending.w, pending.h, pending.mode)
    }
  }

  return {
    layoutCoordinator,
    initComputeLayout,
  }
}
