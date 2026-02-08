import { computed, reactive } from 'vue'

import type { LayoutNodeLike as BaseLayoutNodeLike } from '../types/layout'

const MAX_FLOATING_LABELS = 120

export type HoveredEdgeState = {
  key: string | null
  fromId: string
  toId: string
  amountText: string
  screenX: number
  screenY: number
}

export type FloatingLabel = {
  id: number
  nodeId: string
  offsetXPx?: number
  offsetYPx?: number
  text: string
  color: string
  expiresAtMs: number

  // Optional metadata used for throttling/coalescing.
  throttleKey?: string

  // Optional CSS class for premium/special label styling.
  cssClass?: string

  // Internal: frozen world-space position (set once on first layout resolution).
  // Prevents jitter from ongoing physics updates after the label spawns.
  _frozenX?: number
  _frozenY?: number
}

export type FloatingLabelView = { id: number; x: number; y: number; text: string; color: string; cssClass?: string }

export type LayoutNodeLike = BaseLayoutNodeLike

type UseOverlayStateDeps<N extends LayoutNodeLike> = {
  getLayoutNodeById: (id: string) => N | undefined
  sizeForNode: (n: N) => { w: number; h: number }
  getCameraZoom: () => number

  setFlash: (v: number) => void
  resetFxState: () => void

  nowMs?: () => number
}

export function useOverlayState<N extends LayoutNodeLike>(deps: UseOverlayStateDeps<N>) {
  const hoveredEdge = reactive<HoveredEdgeState>({
    key: null,
    fromId: '',
    toId: '',
    amountText: '',
    screenX: 0,
    screenY: 0,
  })

  function clearHoveredEdge() {
    hoveredEdge.key = null
  }

  const activeEdges = reactive(new Map<string, number>())

  const activeNodes = reactive(new Set<string>())

  const activeEdgeExpiresAtMsByKey = new Map<string, number>()
  let lastActiveEdgePruneAtMs = 0

  const activeNodeExpiresAtMsById = new Map<string, number>()
  let lastActiveNodePruneAtMs = 0

  // Fade duration for the smooth tail of active-edge highlights.
  const ACTIVE_EDGE_FADE_MS = 1200

  function addActiveEdge(key: string, ttlMs = 2000) {
    const nowMs = deps.nowMs ? deps.nowMs() : performance.now()
    const expiresAtMs = nowMs + Math.max(0, ttlMs)

    activeEdges.set(key, 1.0)

    // Touch for LRU (Map preserves insertion order).
    activeEdgeExpiresAtMsByKey.delete(key)
    activeEdgeExpiresAtMsByKey.set(key, expiresAtMs)
  }

  function pruneActiveEdges(nowMs: number) {
    // Keeps activeEdges bounded over long sessions.
    const pruneEveryMs = 250
    const maxEntries = 2500
    if (activeEdgeExpiresAtMsByKey.size === 0) return

    // Throttle only when the map is large; small maps are cheap and should prune immediately.
    if (activeEdgeExpiresAtMsByKey.size > 500 && nowMs - lastActiveEdgePruneAtMs < pruneEveryMs) return
    lastActiveEdgePruneAtMs = nowMs

    for (const [k, exp] of activeEdgeExpiresAtMsByKey) {
      if (nowMs >= exp) {
        // Fully expired — remove.
        activeEdgeExpiresAtMsByKey.delete(k)
        activeEdges.delete(k)
        continue
      }
      // Smooth fade: ramp alpha from 1→0 over the last ACTIVE_EDGE_FADE_MS.
      const remaining = exp - nowMs
      if (remaining < ACTIVE_EDGE_FADE_MS) {
        const alpha = remaining / ACTIVE_EDGE_FADE_MS // 1→0
        activeEdges.set(k, alpha)
      }
      // else: alpha stays at 1.0 (set on addActiveEdge)
    }

    if (activeEdgeExpiresAtMsByKey.size > maxEntries) {
      // Drop oldest entries (Map preserves insertion order).
      const overflow = activeEdgeExpiresAtMsByKey.size - maxEntries
      let dropped = 0
      for (const k of activeEdgeExpiresAtMsByKey.keys()) {
        activeEdgeExpiresAtMsByKey.delete(k)
        activeEdges.delete(k)
        dropped++
        if (dropped >= overflow) break
      }
    }
  }

  function addActiveNode(id: string, ttlMs = 2000) {
    const nowMs = deps.nowMs ? deps.nowMs() : performance.now()
    const expiresAtMs = nowMs + Math.max(0, ttlMs)
    const key = String(id ?? '').trim()
    if (!key) return

    activeNodes.add(key)

    // Touch for LRU (Map preserves insertion order).
    activeNodeExpiresAtMsById.delete(key)
    activeNodeExpiresAtMsById.set(key, expiresAtMs)
  }

  function pruneActiveNodes(nowMs: number) {
    const pruneEveryMs = 250
    const maxEntries = 2500
    if (activeNodeExpiresAtMsById.size === 0) return

    if (activeNodeExpiresAtMsById.size > 500 && nowMs - lastActiveNodePruneAtMs < pruneEveryMs) return
    lastActiveNodePruneAtMs = nowMs

    for (const [id, exp] of activeNodeExpiresAtMsById) {
      if (nowMs < exp) continue
      activeNodeExpiresAtMsById.delete(id)
      activeNodes.delete(id)
    }

    if (activeNodeExpiresAtMsById.size > maxEntries) {
      const overflow = activeNodeExpiresAtMsById.size - maxEntries
      let dropped = 0
      for (const id of activeNodeExpiresAtMsById.keys()) {
        activeNodeExpiresAtMsById.delete(id)
        activeNodes.delete(id)
        dropped++
        if (dropped >= overflow) break
      }
    }
  }

  const floatingLabels = reactive<FloatingLabel[]>([])

  // NOTE: floating labels are keyed by `id` in Vue. In real-mode we can push multiple
  // labels within the same millisecond, so `Math.floor(nowMs)` is not safe.
  // Use a monotonic counter to guarantee uniqueness.
  let nextFloatingLabelId = 1

  const floatingLabelThrottleAtMsByKey = new Map<string, number>()
  let lastFloatingLabelThrottlePruneAtMs = 0

  function pruneFloatingLabelThrottle(nowMs: number) {
    // Keeps the throttle map bounded over long sessions.
    // Values are last-used timestamps; old keys can be dropped safely.
    const pruneEveryMs = 5000
    const ttlMs = 60_000
    const maxEntries = 2000
    if (nowMs - lastFloatingLabelThrottlePruneAtMs < pruneEveryMs && floatingLabelThrottleAtMsByKey.size <= maxEntries) return
    lastFloatingLabelThrottlePruneAtMs = nowMs

    for (const [k, t] of floatingLabelThrottleAtMsByKey) {
      if (nowMs - t > ttlMs) floatingLabelThrottleAtMsByKey.delete(k)
    }

    if (floatingLabelThrottleAtMsByKey.size > maxEntries) {
      // Drop oldest entries (Map preserves insertion order).
      const overflow = floatingLabelThrottleAtMsByKey.size - maxEntries
      let dropped = 0
      for (const k of floatingLabelThrottleAtMsByKey.keys()) {
        floatingLabelThrottleAtMsByKey.delete(k)
        dropped++
        if (dropped >= overflow) break
      }
    }
  }

  function pushFloatingLabel(opts: {
    nodeId: string
    id?: number
    text: string
    color: string
    ttlMs?: number
    offsetXPx?: number
    offsetYPx?: number
    throttleKey?: string
    throttleMs?: number
    cssClass?: string
  }) {
    const nowMs = deps.nowMs ? deps.nowMs() : performance.now()
    pruneFloatingLabelThrottle(nowMs)

    const ttlMs = opts.ttlMs ?? 2200

    const key = opts.throttleKey
    const throttleMs = opts.throttleMs ?? 0
    const lastAt = key && throttleMs > 0 ? (floatingLabelThrottleAtMsByKey.get(key) ?? -Infinity) : -Infinity
    const isThrottleHit = Boolean(key && throttleMs > 0 && nowMs - lastAt < throttleMs)

    if (isThrottleHit && key) {
      // Soft-throttle: if a label with the same throttleKey is already on screen,
      // refresh/update it instead of silently dropping the update.
      for (let i = floatingLabels.length - 1; i >= 0; i--) {
        const fl = floatingLabels[i]!
        if (fl.throttleKey !== key) continue
        fl.text = opts.text
        fl.color = opts.color
        fl.offsetXPx = opts.offsetXPx ?? 0
        fl.offsetYPx = opts.offsetYPx ?? 0
        fl.expiresAtMs = Math.max(fl.expiresAtMs, nowMs + ttlMs)
        return
      }
      // Throttle hit, but the previous label was already pruned/evicted.
      // Do NOT touch the throttle timestamp (keep the window anchored), but do emit a new label.
    }

    if (!isThrottleHit && key && throttleMs > 0) {
      // Touch for LRU (Map preserves insertion order).
      floatingLabelThrottleAtMsByKey.delete(key)
      floatingLabelThrottleAtMsByKey.set(key, nowMs)
    }

    // NOTE: do not drop labels when the node isn't in the layout yet.
    // The view layer will ignore them until the node appears; this prevents losing labels
    // during physics warmup / layout churn.

    // Bound memory/DOM work: keep only the most recent labels.
    // Real-mode can burst during SSE reconnects/replays; higher cap reduces UX dropouts.
      if (floatingLabels.length >= MAX_FLOATING_LABELS) {
        floatingLabels.splice(0, floatingLabels.length - MAX_FLOATING_LABELS + 1)
    }

    const id = opts.id ?? nextFloatingLabelId++
    if (opts.id != null) nextFloatingLabelId = Math.max(nextFloatingLabelId, opts.id + 1)

    floatingLabels.push({
      id,
      nodeId: opts.nodeId,
      offsetXPx: opts.offsetXPx ?? 0,
      offsetYPx: opts.offsetYPx ?? 0,
      text: opts.text,
      color: opts.color,
      expiresAtMs: nowMs + ttlMs,
      throttleKey: opts.throttleKey,
      cssClass: opts.cssClass,
    })
  }

  function pruneFloatingLabels(nowMs: number) {
    // Prevent unbounded DOM growth (labels are transient).
    if (floatingLabels.length > 0) {
      let write = 0
      for (let read = 0; read < floatingLabels.length; read++) {
        const fl = floatingLabels[read]!
        if (nowMs >= fl.expiresAtMs) continue
        floatingLabels[write++] = fl
      }
      floatingLabels.length = write

      // Must match the cap in pushFloatingLabel (120) to avoid killing fresh labels on every frame.
        if (floatingLabels.length > MAX_FLOATING_LABELS) {
          // Drop oldest labels first.
          floatingLabels.splice(0, floatingLabels.length - MAX_FLOATING_LABELS)
      }
    }
  }

  const floatingLabelsView = computed((): FloatingLabelView[] => {
    const out: FloatingLabelView[] = []
    const z = Math.max(0.01, deps.getCameraZoom())

    for (const fl of floatingLabels) {
      // Use frozen position if already resolved (prevents jitter from ongoing physics).
      if (fl._frozenX != null && fl._frozenY != null) {
        out.push({ id: fl.id, x: fl._frozenX, y: fl._frozenY, text: fl.text, color: fl.color, cssClass: fl.cssClass })
        continue
      }

      const ln = deps.getLayoutNodeById(fl.nodeId)
      if (!ln) continue

      // Anchor slightly above the node’s top edge (in screen-space px).
      const sz = deps.sizeForNode(ln)
      const baseOffsetYPx = -(Math.max(sz.w, sz.h) / 2 + 10)
      const dxW = (fl.offsetXPx ?? 0) / z
      const dyW = (baseOffsetYPx + (fl.offsetYPx ?? 0)) / z

      const x = ln.__x + dxW
      const y = ln.__y + dyW

      // Freeze position so subsequent frames don't jitter from physics updates.
      fl._frozenX = x
      fl._frozenY = y

      out.push({
        id: fl.id,
        x,
        y,
        text: fl.text,
        color: fl.color,
        cssClass: fl.cssClass,
      })
    }

    return out
  })

  function resetOverlays() {
    activeEdges.clear()
    activeEdgeExpiresAtMsByKey.clear()
    activeNodes.clear()
    activeNodeExpiresAtMsById.clear()
    floatingLabels.splice(0, floatingLabels.length)
    deps.setFlash(0)
    deps.resetFxState()
  }

  return {
    hoveredEdge,
    clearHoveredEdge,

    activeEdges,
    addActiveEdge,
    pruneActiveEdges,

    activeNodes,
    addActiveNode,
    pruneActiveNodes,

    floatingLabels,
    pushFloatingLabel,
    pruneFloatingLabels,
    floatingLabelsView,

    resetOverlays,
  }
}
