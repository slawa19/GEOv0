import { computed, reactive } from 'vue'

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
}

export type FloatingLabelView = { id: number; x: number; y: number; text: string; color: string }

export type LayoutNodeLike = { __x: number; __y: number }

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

  const activeEdges = reactive(new Set<string>())

  function addActiveEdge(key: string) {
    activeEdges.add(key)
  }

  const floatingLabels = reactive<FloatingLabel[]>([])

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
  }) {
    const nowMs = deps.nowMs ? deps.nowMs() : performance.now()
    pruneFloatingLabelThrottle(nowMs)

    const key = opts.throttleKey
    const throttleMs = opts.throttleMs ?? 0
    if (key && throttleMs > 0) {
      const lastAt = floatingLabelThrottleAtMsByKey.get(key) ?? -Infinity
      if (nowMs - lastAt < throttleMs) return
      // Touch for LRU.
      floatingLabelThrottleAtMsByKey.delete(key)
      floatingLabelThrottleAtMsByKey.set(key, nowMs)
    }

    // Skip if node isn't in the current layout (prevents “teleporting” from stale coords).
    const ln = deps.getLayoutNodeById(opts.nodeId)
    if (!ln) return

    const maxFloatingLabels = 60
    if (floatingLabels.length >= maxFloatingLabels) {
      floatingLabels.splice(0, floatingLabels.length - maxFloatingLabels + 1)
    }

    floatingLabels.push({
      id: opts.id ?? Math.floor(nowMs),
      nodeId: opts.nodeId,
      offsetXPx: opts.offsetXPx ?? 0,
      offsetYPx: opts.offsetYPx ?? 0,
      text: opts.text,
      color: opts.color,
      expiresAtMs: nowMs + (opts.ttlMs ?? 2200),
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

      const maxFloatingLabels = 60
      if (floatingLabels.length > maxFloatingLabels) {
        // Drop oldest labels first.
        floatingLabels.splice(0, floatingLabels.length - maxFloatingLabels)
      }
    }
  }

  const floatingLabelsView = computed((): FloatingLabelView[] => {
    const out: FloatingLabelView[] = []
    const z = Math.max(0.01, deps.getCameraZoom())

    for (const fl of floatingLabels) {
      const ln = deps.getLayoutNodeById(fl.nodeId)
      if (!ln) continue

      // Anchor slightly above the node’s top edge (in screen-space px).
      const sz = deps.sizeForNode(ln)
      const baseOffsetYPx = -(Math.max(sz.w, sz.h) / 2 + 10)
      const dxW = (fl.offsetXPx ?? 0) / z
      const dyW = (baseOffsetYPx + (fl.offsetYPx ?? 0)) / z

      const x = ln.__x + dxW
      const y = ln.__y + dyW

      out.push({
        id: fl.id,
        x,
        y,
        text: fl.text,
        color: fl.color,
      })
    }

    return out
  })

  function resetOverlays() {
    activeEdges.clear()
    deps.setFlash(0)
    deps.resetFxState()
  }

  return {
    hoveredEdge,
    clearHoveredEdge,

    activeEdges,
    addActiveEdge,

    floatingLabels,
    pushFloatingLabel,
    pruneFloatingLabels,
    floatingLabelsView,

    resetOverlays,
  }
}
