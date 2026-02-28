import { computed, reactive, ref } from 'vue'

import { clamp, estimateSizeFromConstraints } from './geometry'
import type {
  WindowAnchor,
  WindowData,
  WindowDataByType,
  WindowGroup,
  WindowInstance,
  WindowManagerApi,
  WindowPolicy,
  WindowSizeConstraints,
  WindowType,
} from './types'

const MAX = 100000

function snap8(v: number): number {
  return Math.round(v / 8) * 8
}

function pickNextActiveId(windowsMap: Map<number, WindowInstance>): number | null {
  let best: { id: number; z: number } | null = null
  for (const [id, win] of windowsMap) {
    if (!best || win.z > best.z) best = { id, z: win.z }
  }
  return best?.id ?? null
}

export function useWindowManager(): WindowManagerApi {
  // Audit fix D-1: стратегия Vue reactivity.
  const windowsMap = reactive(new Map<number, WindowInstance>())
  const focusCounter = ref(0)
  const activeId = ref<number | null>(null)
  const viewport = ref({ width: 0, height: 0 })
  const idCounter = ref(0)

  // NOTE: keep `type` and `data` decoupled at the signature level.
  // `WindowData<T>` doesn't always narrow reliably through generic inference at call sites,
  // so we cast inside each switch branch for stability.
  function getPolicy(type: WindowType, data: WindowData): WindowPolicy {
    switch (type) {
      case 'interact-panel': {
        const d = data as WindowDataByType['interact-panel']
        // MVP placeholder: onEsc может быть замещён на реальную логику flow на следующих шагах.
        const onEsc = () => (d.phase === 'back' ? 'consumed' : 'pass')
        return {
          group: 'interact',
          singleton: 'reuse',
          escBehavior: 'back-then-close',
          closeOnOutsideClick: false,
          onEsc,
        }
      }
      case 'edge-detail':
        return {
          group: 'inspector',
          // Step 5: inspector windows are singleton-by-type, but MUST be 'reuse'
          // to avoid cross-group replace (inspector ↔ interact) side-effects.
          singleton: 'reuse',
          escBehavior: 'close',
          closeOnOutsideClick: true,
        }
      case 'node-card':
        return {
          group: 'inspector',
          singleton: 'reuse',
          escBehavior: 'ignore',
          closeOnOutsideClick: true,
        }
      default: {
        const _exhaustive: never = type
        return _exhaustive
      }
    }
  }

  function getConstraints(type: WindowType, data: WindowData): WindowSizeConstraints {
    switch (type) {
      case 'interact-panel': {
        const d = data as WindowDataByType['interact-panel']
        // Audit fix C-1: trustline preferredWidth MUST be 380.
        const preferredWidth =
          d.panel === 'trustline'
            ? 380
            : d.panel === 'payment'
              ? 420
              : 480

        return {
          minWidth: 320,
          minHeight: 220,
          maxWidth: MAX,
          maxHeight: MAX,
          preferredWidth,
          preferredHeight: 420,
        }
      }
      case 'edge-detail':
        return {
          minWidth: 340,
          minHeight: 200,
          maxWidth: MAX,
          maxHeight: MAX,
          preferredWidth: 420,
          preferredHeight: 320,
        }
      case 'node-card':
        return {
          minWidth: 320,
          minHeight: 180,
          maxWidth: MAX,
          maxHeight: MAX,
          preferredWidth: 360,
          preferredHeight: 260,
        }
      default: {
        const _exhaustive: never = type
        return _exhaustive
      }
    }
  }

  function setActive(next: number | null): void {
    activeId.value = next
    for (const [, win] of windowsMap) win.active = win.id === next
  }

  function focus(id: number): void {
    const win = windowsMap.get(id)
    if (!win) return
    focusCounter.value += 1
    win.z = focusCounter.value
    setActive(id)
  }

  function setViewport(vp: { width: number; height: number }): void {
    viewport.value = { width: vp.width, height: vp.height }
  }

  function updateMeasuredSize(id: number, size: { width: number; height: number }): void {
    const win = windowsMap.get(id)
    if (!win) return
    win.measured = { width: size.width, height: size.height }
  }

  function reclamp(id: number): void {
    const win = windowsMap.get(id)
    if (!win) return
    const vp = viewport.value
    const pad = 12
    const w = win.measured?.width ?? win.rect.width
    const h = win.measured?.height ?? win.rect.height

    // If pinned to anchor, recalculate position from anchor first.
    // IMPORTANT: once the window has been measured (exists in DOM), do NOT overwrite
    // the current rect on reclamp() — this would undo user drags and also breaks
    // singleton='reuse' expectations.
    if (win.anchor && win.placement === 'anchored' && !win.measured) {
      const dx = 16,
        dy = 16
      win.rect.left = win.anchor.x + dx
      win.rect.top = win.anchor.y + dy
    }

    const maxLeft = Math.max(pad, vp.width - w - pad)
    const maxTop = Math.max(pad, vp.height - h - pad)

    // Clamp into viewport bounds.
    win.rect.left = clamp(win.rect.left, pad, maxLeft)
    win.rect.top = clamp(win.rect.top, pad, maxTop)

    // Snap to 8px grid.
    win.rect.left = snap8(win.rect.left)
    win.rect.top = snap8(win.rect.top)

    // Re-clamp after snapping (important when viewport < window size).
    win.rect.left = clamp(win.rect.left, pad, maxLeft)
    win.rect.top = clamp(win.rect.top, pad, maxTop)

    // Update rect dimensions from measured.
    if (win.measured) {
      win.rect.width = win.measured.width
      win.rect.height = win.measured.height
    }
  }

  function reclampAll(): void {
    for (const [id] of windowsMap) reclamp(id)
  }

  function close(id: number, _reason: 'esc' | 'action' | 'programmatic'): void {
    const wasActive = activeId.value === id
    const existed = windowsMap.delete(id)
    if (!existed) return

    if (wasActive) {
      const next = pickNextActiveId(windowsMap)
      setActive(next)
    }
  }

  function closeGroup(g: WindowGroup, reason: 'esc' | 'action' | 'programmatic'): void {
    const ids: number[] = []
    for (const [id, win] of windowsMap) {
      if (win.policy.group === g) ids.push(id)
    }
    for (const id of ids) close(id, reason)
  }

  function open<T extends WindowType>(o: {
    type: T
    anchor?: WindowAnchor | null
    data: WindowData<T>
  }): number {
    const type = o.type
    const data = o.data
    const anchor = o.anchor ?? null

    const policy = getPolicy(type, data)
    const constraints = getConstraints(type, data)

    if (policy.singleton === 'reuse') {
      for (const [id, win] of windowsMap) {
        if (win.type !== type) continue

        const prevAnchor = win.anchor
        const prevRect = { ...win.rect }
        const anchorChanged =
          // If one of them is missing -> changed.
          (!!prevAnchor !== !!anchor) ||
          // If both exist -> compare by value.
          (prevAnchor != null && anchor != null
            ? prevAnchor.x !== anchor.x || prevAnchor.y !== anchor.y || prevAnchor.space !== anchor.space || prevAnchor.source !== anchor.source
            : false)

        win.data = data as unknown as WindowData
        win.anchor = anchor
        win.constraints = constraints
        win.policy = policy
        win.placement = anchor ? 'anchored' : 'docked-right'

        // If window has not been measured yet, refresh rect size estimate.
        if (!win.measured) {
          const est = estimateSizeFromConstraints(constraints)
          win.rect.width = est.width
          win.rect.height = est.height
        }

        // Re-position baseline only when necessary.
        // Feedback: for singleton='reuse', do not override user's dragged position
        // if the window already exists (measured) and anchor did not change.
        if (anchorChanged || !win.measured) {
          if (win.placement === 'anchored' && win.anchor) {
            win.rect.left = win.anchor.x + 16
            win.rect.top = win.anchor.y + 16
          } else {
            win.rect.left = viewport.value.width - win.rect.width - 12
            win.rect.top = 110
          }
        } else {
          // Keep current user position, but allow width/height updates above.
          win.rect.left = prevRect.left
          win.rect.top = prevRect.top
        }

        focus(id)
        reclamp(id)
        return id
      }
    }

    // policy.group mutual exclusion (MVP: only interact windows are exclusive).
    if (policy.group === 'interact') {
      closeGroup(policy.group, 'programmatic')
    }

    const { width, height } = estimateSizeFromConstraints(constraints)
    const placement: WindowInstance['placement'] = anchor ? 'anchored' : 'docked-right'
    const rect =
      placement === 'anchored' && anchor
        ? { left: anchor.x + 16, top: anchor.y + 16, width, height }
        : { left: viewport.value.width - width - 12, top: 110, width, height }

    const id = (idCounter.value += 1)
    const win: WindowInstance = {
      id,
      type,
      policy,
      anchor,
      active: false,
      z: 0,
      placement,
      rect,
      constraints,
      measured: null,
      data: data as unknown as WindowData,
    }
    windowsMap.set(id, win)
    focus(id)
    reclamp(id)
    return id
  }

  function handleEsc(
    ev: KeyboardEvent,
    o: {
      isFormLikeTarget: (t: EventTarget | null) => boolean
      dispatchWindowEsc: () => boolean
    },
  ): boolean {
    if (o.isFormLikeTarget(ev.target)) return false

    // Find topmost (active/max-z) window.
    let top: WindowInstance | null = null
    for (const [, win] of windowsMap) {
      if (!top || win.z > top.z) top = win
    }
    if (!top) return false

    // Give nested content a chance to consume ESC.
    if (o.dispatchWindowEsc()) return true

    const policy = top.policy
    if (policy.escBehavior === 'ignore') return false
    if (policy.escBehavior === 'close') {
      close(top.id, 'esc')
      return true
    }

    // 'back-then-close'
    const r = policy.onEsc?.() ?? 'pass'
    if (r === 'consumed') return true
    close(top.id, 'esc')
    return true
  }

  const windows = computed(() => {
    return Array.from(windowsMap.values()).sort((a, b) => a.z - b.z)
  })

  return {
    windows,
    open,
    close,
    closeGroup,
    focus,
    setViewport,
    updateMeasuredSize,
    reclamp,
    reclampAll,
    handleEsc,
  }
}

