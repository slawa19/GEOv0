import { computed, reactive, ref } from 'vue'

import { clamp, estimateSizeFromConstraints, overlaps } from './geometry'
import { DEFAULT_HUD_STACK_HEIGHT_PX, DEFAULT_WM_CLAMP_PAD_PX } from '../../ui-kit/overlayGeometry'
import type {
  FocusMode,
  WindowAnchor,
  WindowData,
  WindowDataByType,
  WindowGroup,
  WindowInstance,
  WindowManagerApi,
  WindowManagerGeometryPx,
  WindowPolicy,
  WindowSizeConstraints,
  WindowType,
} from './types'

const MAX = 100000

const GROUP_BASE_Z: Record<WindowGroup, number> = {
  // Requirement: interact windows MUST always render above inspector.
  // Use a large gap so focusCounter-based intra-group z never crosses groups.
  inspector: 0,
  interact: 1000000,
}

function snap8(v: number): number {
  return Math.round(v / 8) * 8
}

function snap8InRange(v: number, min: number, max: number): number {
  if (min >= max) return min

  let snapped = snap8(v)
  if (snapped < min) {
    const ceil = Math.ceil(min / 8) * 8
    if (ceil <= max) return ceil
    return min
  }

  if (snapped > max) {
    const floor = Math.floor(max / 8) * 8
    if (floor >= min) return floor
    return max
  }

  return snapped
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
    // P1-3: skip windows in closing state — they're already animating out.
    if (win.state === 'closing') continue
    if (!best || win.effectiveZ > best.z) best = { id, z: win.effectiveZ }
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

  const geometry = ref<WindowManagerGeometryPx>({
    clampPadPx: DEFAULT_WM_CLAMP_PAD_PX,
    dockedRightInsetPx: DEFAULT_WM_CLAMP_PAD_PX,
    dockedRightTopPx: DEFAULT_HUD_STACK_HEIGHT_PX,
  })

  function setGeometry(next: Partial<WindowManagerGeometryPx>): void {
    const prev = geometry.value

    const clampPadPx =
      next.clampPadPx != null && Number.isFinite(next.clampPadPx) && next.clampPadPx >= 0
        ? next.clampPadPx
        : prev.clampPadPx

    const dockedRightInsetPx =
      next.dockedRightInsetPx != null && Number.isFinite(next.dockedRightInsetPx) && next.dockedRightInsetPx >= 0
        ? next.dockedRightInsetPx
        : prev.dockedRightInsetPx

    const dockedRightTopPx =
      next.dockedRightTopPx != null && Number.isFinite(next.dockedRightTopPx) && next.dockedRightTopPx >= 0
        ? next.dockedRightTopPx
        : prev.dockedRightTopPx

    if (
      clampPadPx === prev.clampPadPx &&
      dockedRightInsetPx === prev.dockedRightInsetPx &&
      dockedRightTopPx === prev.dockedRightTopPx
    ) {
      return
    }

    geometry.value = {
      clampPadPx,
      dockedRightInsetPx,
      dockedRightTopPx,
    }
  }

  // UX-6: coalesce rapid bursts of `wm.open()` for singleton='reuse' windows.
  // Scope: node-card reuse updates caused by rapid user interactions.
  // Safety: do NOT debounce focus:'never' updates (these are watcher-driven, e.g. UX-9 anchor-follow).
  const reuseOpenDebounceMs = 90
  let nodeCardReuseTimer: ReturnType<typeof setTimeout> | null = null
  let pendingNodeCardReuse:
    | {
        id: number
        data: WindowDataByType['node-card']
        anchor: WindowAnchor | null
        focusMode: FocusMode
        initiator: Element | null
      }
    | null = null

  // UX-2: Focus-return stack (LIFO).
  // When a window is opened, remember the initiator (current document.activeElement).
  // When that window is closed by user intent, restore focus back to the initiator.
  const focusReturnStack: Array<{ winId: number; initiator: Element | null }> = []

  function captureInitiator(): Element | null {
    try {
      const el = document?.activeElement
      return el instanceof Element ? el : null
    } catch {
      return null
    }
  }

  function isElementConnected(el: Element): boolean {
    // `isConnected` is widely supported; keep a safe fallback for test envs.
    return typeof (el as any).isConnected === 'boolean' ? (el as any).isConnected : document.contains(el)
  }

  function isElementDisabled(el: Element): boolean {
    // Covers HTML form controls; other elements simply won't have `disabled`.
    return 'disabled' in (el as any) ? Boolean((el as any).disabled) : false
  }

  function tryFocus(el: Element | null): boolean {
    if (!el) return false
    if (!isElementConnected(el)) return false
    if (isElementDisabled(el)) return false

    const focusFn = (el as any).focus
    if (typeof focusFn !== 'function') return false

    try {
      // `preventScroll` is supported in modern browsers; tests may ignore it.
      focusFn.call(el, { preventScroll: true })
    } catch {
      try {
        focusFn.call(el)
      } catch {
        return false
      }
    }

    try {
      return document.activeElement === el
    } catch {
      return true
    }
  }

  function pushFocusReturn(winId: number, initiator: Element | null): void {
    focusReturnStack.push({ winId, initiator })
  }

  function takeFocusReturn(winId: number): { winId: number; initiator: Element | null } | null {
    for (let i = focusReturnStack.length - 1; i >= 0; i -= 1) {
      if (focusReturnStack[i]!.winId !== winId) continue
      const [entry] = focusReturnStack.splice(i, 1)
      return entry ?? null
    }
    return null
  }

  function restoreFocusAfterClose(o: { winId: number; reason: 'esc' | 'action' | 'programmatic' }): void {
    // Important: do NOT steal focus during WM-internal programmatic closes
    // (e.g. group-exclusivity close triggered by open()).
    const entry = takeFocusReturn(o.winId)
    if (o.reason === 'programmatic') return

    const ok = tryFocus(entry?.initiator ?? null)
    if (ok) return

    // Fallback: best-effort focus to a stable target.
    tryFocus(document.body)
  }

  function computeEffectiveZ(win: WindowInstance): number {
    return GROUP_BASE_Z[win.policy.group] + win.z
  }

  function closeGroupExcept(g: WindowGroup, exceptId: number, reason: 'esc' | 'action' | 'programmatic'): void {
    const ids: number[] = []
    for (const [id, win] of windowsMap) {
      if (id === exceptId) continue
      if (win.state === 'closing') continue
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

          // Safety fallback: if `onBack` isn't wired, default to UI-close.
          return 'pass'
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

        // PERF-4: phase-aware constraints.
        // Goal: ensure the first-frame estimate (and maxHeight/overflow baseline)
        // matches the panel content phase better to reduce size jumps and reclamp churn.
        // NOTE: `InteractPanelData.phase` is a string; we intentionally keep heuristics
        // permissive and scoped to interact panels only.
        const preferredHeight = (() => {
          const p = String(d.phase ?? '')
          // Treat explicit loading phases (or clearing running/preview) as the smallest.
          if (p.includes('loading') || p.endsWith('-running') || p.endsWith('-preview')) return 260
          // Confirm steps are typically more compact than picking lists.
          if (p.startsWith('confirm-')) return 360
          // Picking steps are the default interact height.
          if (p.startsWith('picking-')) return 420
          // Unknown/custom phases: keep legacy value to avoid changing behavior broadly.
          return 420
        })()

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
          preferredHeight,
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
    win.effectiveZ = computeEffectiveZ(win)
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

    // PERF-2: skip state updates if measurement is unchanged.
    // NOTE: we still allow the first measurement to set `measured` from null.
    const prev = win.measured
    if (prev && prev.width === size.width && prev.height === size.height) return

    win.measured = { width: size.width, height: size.height }
  }

  function reclamp(id: number): void {
    const win = windowsMap.get(id)
    if (!win) return
    const vp = viewport.value
    const pad = geometry.value.clampPadPx

    const before = {
      left: win.rect.left,
      top: win.rect.top,
      width: win.rect.width,
      height: win.rect.height,
    }

    const measured = win.measured
    const w = measured?.width ?? before.width
    const h = measured?.height ?? before.height

    let nextLeft = before.left
    let nextTop = before.top
    let nextWidth = before.width
    let nextHeight = before.height

    // If pinned to anchor, recalculate position from anchor first.
    // IMPORTANT: once the window has been measured (exists in DOM), do NOT overwrite
    // the current rect on reclamp() — this would undo user drags and also breaks
    // singleton='reuse' expectations.
    if (win.anchor && win.placement === 'anchored' && !measured) {
      const dx = win.anchorOffset?.x ?? 16
      const dy = win.anchorOffset?.y ?? 16
      nextLeft = win.anchor.x + dx
      nextTop = win.anchor.y + dy
    }

    const maxLeft = Math.max(pad, vp.width - w - pad)
    const maxTop = Math.max(pad, vp.height - h - pad)

    if (!measured) {
      // Initial placement (estimated size): establish a clean snap-aligned position.
      nextLeft = clamp(nextLeft, pad, maxLeft)
      nextTop = clamp(nextTop, pad, maxTop)

      // Snap to 8px grid.
      nextLeft = snap8(nextLeft)
      nextTop = snap8(nextTop)

      // Re-clamp after snapping (important when viewport < window size).
      nextLeft = clamp(nextLeft, pad, maxLeft)
      nextTop = clamp(nextTop, pad, maxTop)
    } else {
      // Post-measurement reclamp: preserve position unless the window has drifted out of
      // viewport bounds (e.g. due to viewport resize or content growth pushing it out-of-bounds).
      //
      // IMPORTANT: do NOT re-apply snap8 here.
      // When measured size changes (e.g. interact-panel UPDATING→loaded: participants list
      // appears, panel grows from ~100px to ~280px), maxLeft/maxTop shift. Any re-snap based
      // on the new bounds can produce a different value → visible position jump.
      // Fix: only clamp if the current rect is out of bounds; keep exact user position otherwise.
      const clampedLeft = clamp(nextLeft, pad, maxLeft)
      const clampedTop = clamp(nextTop, pad, maxTop)

      const didClamp = clampedLeft !== nextLeft || clampedTop !== nextTop

      if (!didClamp) {
        // Strategy C: keep exact coordinates when already in bounds.
      } else {
        // Snap-on-clamp: if we had to push the window into bounds, align to 8px grid
        // while staying within [pad, max] constraints.
        nextLeft = snap8InRange(clampedLeft, pad, maxLeft)
        nextTop = snap8InRange(clampedTop, pad, maxTop)
      }
    }

    // Update rect dimensions from measured.
    if (measured) {
      nextWidth = measured.width
      nextHeight = measured.height
    }

    // PERF-2: avoid no-op reactive writes (skip commit) when geometry is unchanged.
    if (
      nextLeft === before.left &&
      nextTop === before.top &&
      nextWidth === before.width &&
      nextHeight === before.height
    ) {
      return
    }

    if (nextLeft !== before.left) win.rect.left = nextLeft
    if (nextTop !== before.top) win.rect.top = nextTop
    if (nextWidth !== before.width) win.rect.width = nextWidth
    if (nextHeight !== before.height) win.rect.height = nextHeight
  }

  function reclampAll(): void {
    for (const [id] of windowsMap) reclamp(id)
  }

  function finishClose(id: number): void {
    // P1-3: actual removal from map; idempotent.
    windowsMap.delete(id)

    // UX-2 safety: if a window disappears without a normal close() path,
    // purge any stale focus-return entries.
    takeFocusReturn(id)
  }

  function close(id: number, _reason: 'esc' | 'action' | 'programmatic'): void {
    const win = windowsMap.get(id)
    if (!win) return
    // P1-3: already in closing — don't process twice.
    if (win.state === 'closing') return

    win.state = 'closing'

    try {
      win.policy?.onClose?.(_reason)
    } catch {
      // Best-effort: closing a window must not throw.
    }

    const wasActive = activeId.value === id
    if (wasActive) {
      const next = pickNextActiveId(windowsMap)
      setActive(next)
    }

    restoreFocusAfterClose({ winId: id, reason: _reason })

    // P1-3: fallback safety — remove from map if @after-leave never fires (e.g. reduced-motion, no transition).
    setTimeout(() => finishClose(id), 350)
  }

  function closeGroup(g: WindowGroup, reason: 'esc' | 'action' | 'programmatic'): void {
    const ids: number[] = []
    for (const [id, win] of windowsMap) {
      if (win.state === 'closing') continue
      if (win.policy.group === g) ids.push(id)
    }
    for (const id of ids) close(id, reason)
  }

  function closeByType(type: WindowType, reason: 'esc' | 'action' | 'programmatic'): number {
    const ids: number[] = []
    for (const [id, win] of windowsMap) {
      if (win.state === 'closing') continue
      if (win.type === type) ids.push(id)
    }
    for (const id of ids) close(id, reason)
    return ids.length
  }

  function flushPendingNodeCardReuse(): void {
    // Clear timer first so nested open() calls can't cancel this flush.
    if (nodeCardReuseTimer !== null) {
      clearTimeout(nodeCardReuseTimer)
      nodeCardReuseTimer = null
    }

    const pending = pendingNodeCardReuse
    pendingNodeCardReuse = null
    if (!pending) return

    const win = windowsMap.get(pending.id)
    if (!win) return
    // P1-3: never touch closing windows.
    if (win.state === 'closing') return
    if (win.type !== 'node-card') return

    const data = pending.data
    const anchor = pending.anchor
    const policy = getPolicy('node-card', data)
    const constraints = getConstraints('node-card', data)

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
    // Policy may change group; update visual stacking base accordingly.
    win.effectiveZ = computeEffectiveZ(win)
    win.placement = anchor ? 'anchored' : 'docked-right'

    // If window has not been measured yet, refresh rect size estimate.
    if (!win.measured) {
      const est = estimateSizeFromConstraints(constraints)
      win.rect.width = est.width
      win.rect.height = est.height
    }

    // Re-position baseline only when necessary.
    if (anchorChanged || !win.measured) {
      if (win.placement === 'anchored' && win.anchor) {
        win.anchorOffset = { x: 16, y: 16 }
        win.rect.left = win.anchor.x + win.anchorOffset.x
        win.rect.top = win.anchor.y + win.anchorOffset.y
      } else {
        win.anchorOffset = null
        win.rect.left = viewport.value.width - win.rect.width - geometry.value.dockedRightInsetPx
        win.rect.top = geometry.value.dockedRightTopPx
      }
    } else {
      // Keep current user position, but allow width/height updates above.
      win.rect.left = prevRect.left
      win.rect.top = prevRect.top
    }

    // policy.group mutual exclusion (MVP): any group is exclusive.
    // Spec: `interact` is singleton; `inspector` is NodeCard XOR EdgeDetail.
    closeGroupExcept(policy.group, pending.id, 'programmatic')

    // Collision avoidance for the initial rect (before the window is measured/user-dragged).
    if (!win.measured) {
      const others = Array.from(windowsMap.values())
        .filter((w) => w.id !== pending.id)
        .map((w) => w.rect)
      const next = cascadeShiftAvoidOverlaps({ rect: win.rect, others })
      win.rect.left = next.left
      win.rect.top = next.top
      if (win.anchor && win.placement === 'anchored') {
        win.anchorOffset = { x: win.rect.left - win.anchor.x, y: win.rect.top - win.anchor.y }
      }
    }

    // focus control on reuse:
    // 'always' → always focus; 'never' → skip; 'auto' (default) → skip.
    if (pending.focusMode === 'always') {
      // UX-2: treat user-initiated "bring to front" as a fresh open for focus-return.
      pushFocusReturn(pending.id, pending.initiator)
      focus(pending.id)
    }

    reclamp(pending.id)
  }

  function open<T extends WindowType>(o: {
    type: T
    anchor?: WindowAnchor | null
    data: WindowData<T>
    /**
     * Controls z-order / active change on reuse.
     * - `'auto'` (default): focus on creation only; reuse without focus.
     * - `'always'`: always focus (user-initiated actions).
     * - `'never'`: never focus (reactive/watcher-driven updates).
     */
    focus?: FocusMode
  }): number {
    const initiator = captureInitiator()
    const type = o.type
    const data = o.data
    const anchor = o.anchor ?? null
    const focusMode = o.focus ?? 'auto'

    const policy = getPolicy(type, data)
    const constraints = getConstraints(type, data)

    if (policy.singleton === 'reuse') {
      for (const [id, win] of windowsMap) {
        if (win.type !== type) continue
        // P1-3: skip windows in closing state — they're animating out, should not be reused.
        if (win.state === 'closing') continue

        // UX-6: debounce rapid node-card reuse updates (apply only the trailing payload).
        // IMPORTANT: do NOT debounce focus:'never' (UX-9 anchor-follow updates).
        if (type === 'node-card' && focusMode !== 'never') {
          pendingNodeCardReuse = {
            id,
            data: data as unknown as WindowDataByType['node-card'],
            anchor,
            focusMode,
            initiator,
          }
          if (nodeCardReuseTimer !== null) clearTimeout(nodeCardReuseTimer)
          nodeCardReuseTimer = setTimeout(flushPendingNodeCardReuse, reuseOpenDebounceMs)
          return id
        }

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
        // Policy may change group; update visual stacking base accordingly.
        win.effectiveZ = computeEffectiveZ(win)
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
            win.rect.left = viewport.value.width - win.rect.width - geometry.value.dockedRightInsetPx
            win.rect.top = geometry.value.dockedRightTopPx
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

        // focus control on reuse:
        // 'always' → always focus; 'never' → skip; 'auto' (default) → skip (created already focused)
        if (focusMode === 'always') {
          // UX-2: treat user-initiated "bring to front" as a fresh open for focus-return.
          pushFocusReturn(id, initiator)
          focus(id)
        }
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
        : {
            left: viewport.value.width - width - geometry.value.dockedRightInsetPx,
            top: geometry.value.dockedRightTopPx,
            width,
            height,
          }

    const id = (idCounter.value += 1)
    const win: WindowInstance = {
      id,
      type,
      state: 'open',
      policy,
      anchor,
      anchorOffset,
      active: false,
      z: 0,
      effectiveZ: 0,
      placement,
      rect,
      constraints,
      measured: null,
      data: data as unknown as WindowData,
    }
    windowsMap.set(id, win)

    // UX-2: remember the focus initiator for later restore on close().
    pushFocusReturn(id, initiator)

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

    // Find topmost (visual max effectiveZ) window — skip closing windows (P1-3).
    let top: WindowInstance | null = null
    for (const [, win] of windowsMap) {
      if (win.state === 'closing') continue
      if (!top || win.effectiveZ > top.effectiveZ) top = win
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
    // P1-3: exclude 'closing' windows so TransitionGroup triggers leave-animation when close() is called.
    // 'closing' windows remain in windowsMap until finishClose() is called (via @after-leave or fallback timer).
    return Array.from(windowsMap.values())
      .filter((w) => w.state === 'open')
      .sort((a, b) => a.effectiveZ - b.effectiveZ)
  })

  function getTopmostInGroup(g: WindowGroup): WindowInstance | null {
    // Prefer WM active window when it's in the requested group (P1-3: skip closing).
    for (const [, win] of windowsMap) {
      if (win.state === 'closing') continue
      if (win.policy.group === g && win.active) return win
    }

    // Fallback: max effectiveZ within the group (P1-3: skip closing).
    let top: WindowInstance | null = null
    for (const [, win] of windowsMap) {
      if (win.state === 'closing') continue
      if (win.policy.group !== g) continue
      if (!top || win.effectiveZ > top.effectiveZ) top = win
    }
    return top
  }

  return {
    windows,
    setGeometry,
    getTopmostInGroup,
    open,
    close,
    finishClose,
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
