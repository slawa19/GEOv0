import { computed, type ComputedRef } from 'vue'

import type { LayoutNodeLike } from '../types/layout'
import type { FloatingLabelView } from './useOverlayState'
import { clamp01 } from '../utils/math'

export type FloatingLabelFx = FloatingLabelView & { glow: number; cssClass?: string }

type UseFloatingLabelsViewFxDeps<N extends LayoutNodeLike> = {
  getFloatingLabelsView: () => FloatingLabelView[]
  isWebDriver: () => boolean

  getLayoutNodes: () => N[]
  sizeForNode: (n: N) => { w: number; h: number }

  worldToScreen: (x: number, y: number) => { x: number; y: number }
}

type UseFloatingLabelsViewFxReturn = {
  floatingLabelsViewFx: ComputedRef<FloatingLabelFx[]>
}

export function useFloatingLabelsViewFx<N extends LayoutNodeLike>(
  deps: UseFloatingLabelsViewFxDeps<N>,
): UseFloatingLabelsViewFxReturn {
  const floatingLabelsViewFx = computed((): FloatingLabelFx[] => {
    const base = deps.getFloatingLabelsView()
    if (base.length === 0) return []

    // Keep Playwright screenshots stable.
    if (deps.isWebDriver()) return base.map((fl) => ({ ...fl, glow: 0 }))

    // Trigger a soft glow when a label is close enough to any node
    // that it could visually merge with it.
    const nodes = deps.getLayoutNodes()
    const padPx = 12
    const falloffPx = 36

    return base.map((fl) => {
      const lp = deps.worldToScreen(fl.x, fl.y)
      let best = 0

      for (const n of nodes) {
        const sz = deps.sizeForNode(n)
        const rPx = Math.max(6, Math.max(sz.w, sz.h) / 2)
        const np = deps.worldToScreen(n.__x, n.__y)

        const dx = lp.x - np.x
        const dy = lp.y - np.y

        // Fast reject.
        if (Math.abs(dx) > rPx + padPx + falloffPx) continue
        if (Math.abs(dy) > rPx + padPx + falloffPx) continue

        const boundary = rPx + padPx
        const d = Math.hypot(dx, dy)
        const t = clamp01(1 - (d - boundary) / falloffPx)
        if (t > best) best = t
        if (best >= 0.98) break
      }

      return { ...fl, glow: best }
    })
  })

  return { floatingLabelsViewFx }
}
