import { computed, getCurrentScope, onScopeDispose, ref, type ComputedRef } from 'vue'

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
  // Throttle heavy `O(labels×nodes)` work. We keep the exact same math, but
  // recompute at most once per ~100ms and schedule a trailing recompute so the
  // view doesn't get stuck if changes stop within the throttle window.
  const gate = ref(0)

  const throttleMs = 100
  let lastComputeAtMs = -Infinity
  let pendingTimer: ReturnType<typeof setTimeout> | null = null
  let glowByLabelId = new Map<number, number>()

  const nowMs = () => (typeof performance !== 'undefined' && performance.now ? performance.now() : Date.now())

  const scheduleTrailingRecompute = (delayMs: number) => {
    if (pendingTimer != null) return
    pendingTimer = setTimeout(() => {
      pendingTimer = null
      gate.value++
    }, delayMs)
  }

  if (getCurrentScope()) {
    onScopeDispose(() => {
      if (pendingTimer != null) clearTimeout(pendingTimer)
      pendingTimer = null
    })
  }

  const floatingLabelsViewFx = computed((): FloatingLabelFx[] => {
    gate.value

    const base = deps.getFloatingLabelsView()
    if (base.length === 0) return []

    // Keep Playwright screenshots stable.
    if (deps.isWebDriver()) return base.map((fl) => ({ ...fl, glow: 0 }))

    const now = nowMs()
    const elapsed = now - lastComputeAtMs
    const shouldRecompute = elapsed >= throttleMs || glowByLabelId.size === 0

    if (shouldRecompute) {
      lastComputeAtMs = now

      // Trigger a soft glow when a label is close enough to any node
      // that it could visually merge with it.
      const nodes = deps.getLayoutNodes()
      const padPx = 12
      const falloffPx = 36

      const next = new Map<number, number>()
      for (const fl of base) {
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

        next.set(fl.id, best)
      }

      glowByLabelId = next
    } else {
      scheduleTrailingRecompute(throttleMs - elapsed)
    }

    // Cheap path on every reactive update: preserve base view updates, reuse last glow.
    return base.map((fl) => ({ ...fl, glow: glowByLabelId.get(fl.id) ?? 0 }))
  })

  return { floatingLabelsViewFx }
}
