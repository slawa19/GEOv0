import { computed, reactive, ref } from 'vue'

import { clamp, estimateSizeFromConstraints, overlaps } from './geometry'
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

function cascadeShiftAvoidOverlaps(o: {
  rect: { left: number; top: number; width: number; height: number }
  others: Array<{ left: number; top: number; width: number; height: number }>
  maxAttempts?: number
  step?: number
}): { left: number; top: number } {
  const maxAttempts = o.maxAttempts ?? 24
  const step = o.step ?? 32

  let left = o.rect.left
  let top = o.rect.top

  for (let i = 0; i < maxAttempts; i += 1) {
    const candidate = { left, top, width: o.rect.width, height: o.rect.height }
    const hit = o.others.some((r) => overlaps(candidate, r))
    if (!hit) break
    left += step
    top += step
  }

  return { left, top }
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

  function closeGroupExcept(g: WindowGroup, exceptId: number, reason: 'esc' | 'action' | 'programmatic'): void {
    const ids: number[] = []
    for (const [id, win] of windowsMap) {
      if (id === exceptId) continue
      if (win.policy.group === g) ids.push(id)
    }
    for (const id of ids) close(id, reason)
  }

  // NOTE: keep `type` and `data` decoupled at the signature level.
  // `WindowData<T>` doesn't always narrow reliably through generic inference at call sites,
  // so we cast inside each switch branch for stability.
  function getPolicy(type: WindowType, data: WindowData): WindowPolicy {
    switch (type) {
      case 'interact-panel': {
        const d = data as WindowDataByType['interact-panel']
        // Step-back for Interact windows: try to move Interact FSM back first, otherwise allow UI-close.
        const onEsc = () => {
          try {
            if (typeof d.onBack === 'function') {
              return d.onBack() ? 'consumed' : 'pass'
            }
          } catch {
            // Best-effort: never block UI-close on errors.
          }

          // Legacy fallback (pre-step-back wiring): keep previous placeholder semantics.
          return d.phase === 'back' ? 'consumed' : 'pass'
        }
        return {
          group: 'interact',
          singleton: 'reuse',
          escBehavior: 'back-then-close',
          closeOnOutsideClick: false,
          onEsc,
          onClose: (reason) => {
            // UI-close (ESC at first step / [×]) must cancel the interact flow
            // to avoid "busy without window" state.
            if (reason === 'esc' || reason === 'action') {
              try { d.onClose?.() } catch { /* best-effort */ }
            }
          },
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
          onClose: (reason) => {
            try {
              ;(data as WindowDataByType['edge-detail'])?.onClose?.(reason)
            } catch {
              // no-op
            }
          },
        }
      case 'node-card':
        return {
          group: 'inspector',
          singleton: 'reuse',
          // ESC/back-stack: node-card participates in window stack.
          escBehavior: 'close',
          closeOnOutsideClick: true,
          onClose: (reason) => {
            try {
              ;(data as WindowDataByType['node-card'])?.onClose?.(reason)
            } catch {
              // no-op
            }
          },
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
        // IMPORTANT: payment/clearing preferredWidth MUST match `.ds-ov-panel` max-width (560px),
        // otherwise WindowShell will render with an incorrect first-frame estimate and then visibly
        // jump after the first ResizeObserver measurement + reclamp.
        const preferredWidth =
          d.panel === 'trustline'
            ? 380
            : d.panel === 'payment'
              ? 560
              : 560

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

    // Safety: ignore invalid measurements (e.g. 0x0 in tests or during initial mount)
    // to avoid collapsing `rect` in reclamp().
    if (!(size.width > 0) || !(size.height > 0)) return

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
      const dx = win.anchorOffset?.x ?? 16
      const dy = win.anchorOffset?.y ?? 16
      win.rect.left = win.anchor.x + dx
      win.rect.top = win.anchor.y + dy
    }

    const maxLeft = Math.max(pad, vp.width - w - pad)
    const maxTop = Math.max(pad, vp.height - h - pad)

    if (!win.measured) {
      // Initial placement (estimated size): establish a clean snap-aligned position.
      win.rect.left = clamp(win.rect.left, pad, maxLeft)
      win.rect.top = clamp(win.rect.top, pad, maxTop)

      // Snap to 8px grid.
      win.rect.left = snap8(win.rect.left)
      win.rect.top = snap8(win.rect.top)

      // Re-clamp after snapping (important when viewport < window size).
      win.rect.left = clamp(win.rect.left, pad, maxLeft)
      win.rect.top = clamp(win.rect.top, pad, maxTop)
    } else {
      // Post-measurement reclamp: preserve position unless the window has drifted out of
      // viewport bounds (e.g. due to viewport resize or content growth pushing it out-of-bounds).
      //
      // IMPORTANT: do NOT re-apply snap8 here.
      // When measured size changes (e.g. interact-panel UPDATING→loaded: participants list
      // appears, panel grows from ~100px to ~280px), maxLeft/maxTop shift. Any re-snap based
      // on the new bounds can produce a different value → visible position jump.
      // Fix: only clamp if the current rect is out of bounds; keep exact user position otherwise.
      const clampedLeft = clamp(win.rect.left, pad, maxLeft)
      if (clampedLeft !== win.rect.left) win.rect.left = clampedLeft

      const clampedTop = clamp(win.rect.top, pad, maxTop)
      if (clampedTop !== win.rect.top) win.rect.top = clampedTop
    }

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
    const win = windowsMap.get(id)
    const existed = windowsMap.delete(id)
    if (!existed) return

    try {
      win?.policy?.onClose?.(_reason)
    } catch {
      // Best-effort: closing a window must not throw.
    }

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

  function closeByType(type: WindowType, reason: 'esc' | 'action' | 'programmatic'): number {
    const ids: number[] = []
    for (const [id, win] of windowsMap) {
      if (win.type === type) ids.push(id)
    }
    for (const id of ids) close(id, reason)
    return ids.length
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
            win.anchorOffset = { x: 16, y: 16 }
            win.rect.left = win.anchor.x + win.anchorOffset.x
            win.rect.top = win.anchor.y + win.anchorOffset.y
          } else {
            win.anchorOffset = null
            win.rect.left = viewport.value.width - win.rect.width - 12
            win.rect.top = 110
          }
        } else {
          // Keep current user position, but allow width/height updates above.
          win.rect.left = prevRect.left
          win.rect.top = prevRect.top
        }

        // policy.group mutual exclusion (MVP): any group is exclusive.
        // Spec: `interact` is singleton; `inspector` is NodeCard XOR EdgeDetail.
        closeGroupExcept(policy.group, id, 'programmatic')

        // Collision avoidance for the initial rect (before the window is measured/user-dragged).
        if (!win.measured) {
          const others = Array.from(windowsMap.values())
            .filter((w) => w.id !== id)
            .map((w) => w.rect)
          const next = cascadeShiftAvoidOverlaps({ rect: win.rect, others })
          win.rect.left = next.left
          win.rect.top = next.top
          if (win.anchor && win.placement === 'anchored') {
            win.anchorOffset = { x: win.rect.left - win.anchor.x, y: win.rect.top - win.anchor.y }
          }
        }

        focus(id)
        reclamp(id)
        return id
      }
    }

    // policy.group mutual exclusion (MVP): any group is exclusive.
    // Spec: `interact` is singleton; `inspector` is NodeCard XOR EdgeDetail.
    closeGroup(policy.group, 'programmatic')

    const { width, height } = estimateSizeFromConstraints(constraints)
    const placement: WindowInstance['placement'] = anchor ? 'anchored' : 'docked-right'
    const anchorOffset = placement === 'anchored' && anchor ? { x: 16, y: 16 } : null
    const rect =
      placement === 'anchored' && anchor
        ? { left: anchor.x + anchorOffset!.x, top: anchor.y + anchorOffset!.y, width, height }
        : { left: viewport.value.width - width - 12, top: 110, width, height }

    const id = (idCounter.value += 1)
    const win: WindowInstance = {
      id,
      type,
      policy,
      anchor,
      anchorOffset,
      active: false,
      z: 0,
      placement,
      rect,
      constraints,
      measured: null,
      data: data as unknown as WindowData,
    }
    windowsMap.set(id, win)

    // Collision avoidance for the first-frame estimate rect.
    {
      const others = Array.from(windowsMap.values())
        .filter((w) => w.id !== id)
        .map((w) => w.rect)
      const next = cascadeShiftAvoidOverlaps({ rect: win.rect, others })
      win.rect.left = next.left
      win.rect.top = next.top
      if (win.anchor && win.placement === 'anchored') {
        win.anchorOffset = { x: win.rect.left - win.anchor.x, y: win.rect.top - win.anchor.y }
      }
    }

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
    // Convention: `dispatchWindowEsc()` follows DOM `dispatchEvent` semantics:
    // it returns `false` when a cancelable event was prevented (meaning: consumed).
    const notCanceled = o.dispatchWindowEsc()
    if (!notCanceled) return true

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

  function getTopmostInGroup(g: WindowGroup): WindowInstance | null {
    // Prefer WM active window when it's in the requested group.
    for (const [, win] of windowsMap) {
      if (win.policy.group === g && win.active) return win
    }

    // Fallback: max-z within the group.
    let top: WindowInstance | null = null
    for (const [, win] of windowsMap) {
      if (win.policy.group !== g) continue
      if (!top || win.z > top.z) top = win
    }
    return top
  }

  return {
    windows,
    getTopmostInGroup,
    open,
    close,
    closeByType,
    closeGroup,
    focus,
    setViewport,
    updateMeasuredSize,
    reclamp,
    reclampAll,
    handleEsc,
  }
}
