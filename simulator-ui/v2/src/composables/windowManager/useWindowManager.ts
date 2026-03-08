import { computed, reactive, ref } from 'vue'

import { clamp, estimateSizeFromConstraints, overlaps } from './geometry'
import {
  DEFAULT_HUD_STACK_HEIGHT_PX,
  DEFAULT_VIEWPORT_FALLBACK_HEIGHT_PX,
  DEFAULT_VIEWPORT_FALLBACK_WIDTH_PX,
  DEFAULT_WM_ANCHOR_OFFSET_X_PX,
  DEFAULT_WM_ANCHOR_OFFSET_Y_PX,
  DEFAULT_WM_CASCADE_STEP_PX,
  DEFAULT_WM_CLAMP_PAD_PX,
  DEFAULT_WM_EDGE_DETAIL_MIN_HEIGHT_PX,
  DEFAULT_WM_EDGE_DETAIL_MIN_WIDTH_PX,
  DEFAULT_WM_EDGE_DETAIL_PREFERRED_HEIGHT_PX,
  DEFAULT_WM_EDGE_DETAIL_PREFERRED_WIDTH_PX,
  DEFAULT_WM_INTERACT_MIN_HEIGHT_PX,
  DEFAULT_WM_INTERACT_MIN_WIDTH_PX,
  DEFAULT_WM_INTERACT_PREFERRED_WIDTH_PAYMENT_PX,
  DEFAULT_WM_INTERACT_PREFERRED_HEIGHT_CONFIRM_PX,
  DEFAULT_WM_INTERACT_PREFERRED_HEIGHT_LOADING_PX,
  DEFAULT_WM_INTERACT_PREFERRED_HEIGHT_PICKING_PX,
  DEFAULT_WM_INTERACT_PREFERRED_WIDTH_TRUSTLINE_PX,
  DEFAULT_WM_INTERACT_PREFERRED_WIDTH_WIDE_PX,
  DEFAULT_WM_NODE_CARD_MIN_HEIGHT_PX,
  DEFAULT_WM_NODE_CARD_MIN_WIDTH_PX,
  DEFAULT_WM_NODE_CARD_PREFERRED_HEIGHT_PX,
  DEFAULT_WM_NODE_CARD_PREFERRED_WIDTH_PX,
  DEFAULT_WM_GROUP_Z_INSPECTOR_BASE,
  DEFAULT_WM_GROUP_Z_INTERACT_BASE,
} from '../../ui-kit/overlayGeometry'
import { warnOverlayDiagnostics } from '../../ui-kit/overlayDiagnostics'
import type {
  FocusMode,
  WindowAnchor,
  WindowCloseReason,
  WindowData,
  WindowDataByType,
  WindowOpenArgs,
  WindowGroup,
  WindowInstance,
  WindowManagerApi,
  WindowManagerGeometryPx,
  WindowPolicy,
  WindowSizeConstraints,
  WindowType,
} from './types'
import { isWindowActiveEligible } from './types'

const MAX = 100000

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
  const step = o.step ?? DEFAULT_WM_CASCADE_STEP_PX

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
    if (!isWindowActiveEligible(win)) continue
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

    anchorOffsetXPx: DEFAULT_WM_ANCHOR_OFFSET_X_PX,
    anchorOffsetYPx: DEFAULT_WM_ANCHOR_OFFSET_Y_PX,
    cascadeStepPx: DEFAULT_WM_CASCADE_STEP_PX,

    interactPanelMinWidthPx: DEFAULT_WM_INTERACT_MIN_WIDTH_PX,
    interactPanelMinHeightPx: DEFAULT_WM_INTERACT_MIN_HEIGHT_PX,
    interactPanelPreferredWidthTrustlinePx: DEFAULT_WM_INTERACT_PREFERRED_WIDTH_TRUSTLINE_PX,
    interactPanelPreferredWidthPaymentPx: DEFAULT_WM_INTERACT_PREFERRED_WIDTH_PAYMENT_PX,
    interactPanelPreferredWidthWidePx: DEFAULT_WM_INTERACT_PREFERRED_WIDTH_WIDE_PX,
    interactPanelPreferredHeightLoadingPx: DEFAULT_WM_INTERACT_PREFERRED_HEIGHT_LOADING_PX,
    interactPanelPreferredHeightConfirmPx: DEFAULT_WM_INTERACT_PREFERRED_HEIGHT_CONFIRM_PX,
    interactPanelPreferredHeightPickingPx: DEFAULT_WM_INTERACT_PREFERRED_HEIGHT_PICKING_PX,

    edgeDetailMinWidthPx: DEFAULT_WM_EDGE_DETAIL_MIN_WIDTH_PX,
    edgeDetailMinHeightPx: DEFAULT_WM_EDGE_DETAIL_MIN_HEIGHT_PX,
    edgeDetailPreferredWidthPx: DEFAULT_WM_EDGE_DETAIL_PREFERRED_WIDTH_PX,
    edgeDetailPreferredHeightPx: DEFAULT_WM_EDGE_DETAIL_PREFERRED_HEIGHT_PX,

    nodeCardMinWidthPx: DEFAULT_WM_NODE_CARD_MIN_WIDTH_PX,
    nodeCardMinHeightPx: DEFAULT_WM_NODE_CARD_MIN_HEIGHT_PX,
    nodeCardPreferredWidthPx: DEFAULT_WM_NODE_CARD_PREFERRED_WIDTH_PX,
    nodeCardPreferredHeightPx: DEFAULT_WM_NODE_CARD_PREFERRED_HEIGHT_PX,

    groupZInspectorBase: DEFAULT_WM_GROUP_Z_INSPECTOR_BASE,
    groupZInteractBase: DEFAULT_WM_GROUP_Z_INTERACT_BASE,
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

    const anchorOffsetXPx =
      next.anchorOffsetXPx != null && Number.isFinite(next.anchorOffsetXPx) && next.anchorOffsetXPx >= 0
        ? next.anchorOffsetXPx
        : prev.anchorOffsetXPx

    const anchorOffsetYPx =
      next.anchorOffsetYPx != null && Number.isFinite(next.anchorOffsetYPx) && next.anchorOffsetYPx >= 0
        ? next.anchorOffsetYPx
        : prev.anchorOffsetYPx

    const cascadeStepPx =
      next.cascadeStepPx != null && Number.isFinite(next.cascadeStepPx) && next.cascadeStepPx >= 0
        ? next.cascadeStepPx
        : prev.cascadeStepPx

    const interactPanelMinWidthPx =
      next.interactPanelMinWidthPx != null && Number.isFinite(next.interactPanelMinWidthPx) && next.interactPanelMinWidthPx > 0
        ? next.interactPanelMinWidthPx
        : prev.interactPanelMinWidthPx

    const interactPanelMinHeightPx =
      next.interactPanelMinHeightPx != null && Number.isFinite(next.interactPanelMinHeightPx) && next.interactPanelMinHeightPx > 0
        ? next.interactPanelMinHeightPx
        : prev.interactPanelMinHeightPx

    const interactPanelPreferredWidthTrustlinePx =
      next.interactPanelPreferredWidthTrustlinePx != null &&
      Number.isFinite(next.interactPanelPreferredWidthTrustlinePx) &&
      next.interactPanelPreferredWidthTrustlinePx > 0
        ? next.interactPanelPreferredWidthTrustlinePx
        : prev.interactPanelPreferredWidthTrustlinePx

    const interactPanelPreferredWidthPaymentPx =
      next.interactPanelPreferredWidthPaymentPx != null &&
      Number.isFinite(next.interactPanelPreferredWidthPaymentPx) &&
      next.interactPanelPreferredWidthPaymentPx > 0
        ? next.interactPanelPreferredWidthPaymentPx
        : prev.interactPanelPreferredWidthPaymentPx

    const interactPanelPreferredWidthWidePx =
      next.interactPanelPreferredWidthWidePx != null &&
      Number.isFinite(next.interactPanelPreferredWidthWidePx) &&
      next.interactPanelPreferredWidthWidePx > 0
        ? next.interactPanelPreferredWidthWidePx
        : prev.interactPanelPreferredWidthWidePx

    const interactPanelPreferredHeightLoadingPx =
      next.interactPanelPreferredHeightLoadingPx != null &&
      Number.isFinite(next.interactPanelPreferredHeightLoadingPx) &&
      next.interactPanelPreferredHeightLoadingPx > 0
        ? next.interactPanelPreferredHeightLoadingPx
        : prev.interactPanelPreferredHeightLoadingPx

    const interactPanelPreferredHeightConfirmPx =
      next.interactPanelPreferredHeightConfirmPx != null &&
      Number.isFinite(next.interactPanelPreferredHeightConfirmPx) &&
      next.interactPanelPreferredHeightConfirmPx > 0
        ? next.interactPanelPreferredHeightConfirmPx
        : prev.interactPanelPreferredHeightConfirmPx

    const interactPanelPreferredHeightPickingPx =
      next.interactPanelPreferredHeightPickingPx != null &&
      Number.isFinite(next.interactPanelPreferredHeightPickingPx) &&
      next.interactPanelPreferredHeightPickingPx > 0
        ? next.interactPanelPreferredHeightPickingPx
        : prev.interactPanelPreferredHeightPickingPx

    const edgeDetailMinWidthPx =
      next.edgeDetailMinWidthPx != null && Number.isFinite(next.edgeDetailMinWidthPx) && next.edgeDetailMinWidthPx > 0
        ? next.edgeDetailMinWidthPx
        : prev.edgeDetailMinWidthPx

    const edgeDetailMinHeightPx =
      next.edgeDetailMinHeightPx != null && Number.isFinite(next.edgeDetailMinHeightPx) && next.edgeDetailMinHeightPx > 0
        ? next.edgeDetailMinHeightPx
        : prev.edgeDetailMinHeightPx

    const edgeDetailPreferredWidthPx =
      next.edgeDetailPreferredWidthPx != null &&
      Number.isFinite(next.edgeDetailPreferredWidthPx) &&
      next.edgeDetailPreferredWidthPx > 0
        ? next.edgeDetailPreferredWidthPx
        : prev.edgeDetailPreferredWidthPx

    const edgeDetailPreferredHeightPx =
      next.edgeDetailPreferredHeightPx != null &&
      Number.isFinite(next.edgeDetailPreferredHeightPx) &&
      next.edgeDetailPreferredHeightPx > 0
        ? next.edgeDetailPreferredHeightPx
        : prev.edgeDetailPreferredHeightPx

    const nodeCardMinWidthPx =
      next.nodeCardMinWidthPx != null && Number.isFinite(next.nodeCardMinWidthPx) && next.nodeCardMinWidthPx > 0
        ? next.nodeCardMinWidthPx
        : prev.nodeCardMinWidthPx

    const nodeCardMinHeightPx =
      next.nodeCardMinHeightPx != null && Number.isFinite(next.nodeCardMinHeightPx) && next.nodeCardMinHeightPx > 0
        ? next.nodeCardMinHeightPx
        : prev.nodeCardMinHeightPx

    const nodeCardPreferredWidthPx =
      next.nodeCardPreferredWidthPx != null &&
      Number.isFinite(next.nodeCardPreferredWidthPx) &&
      next.nodeCardPreferredWidthPx > 0
        ? next.nodeCardPreferredWidthPx
        : prev.nodeCardPreferredWidthPx

    const nodeCardPreferredHeightPx =
      next.nodeCardPreferredHeightPx != null &&
      Number.isFinite(next.nodeCardPreferredHeightPx) &&
      next.nodeCardPreferredHeightPx > 0
        ? next.nodeCardPreferredHeightPx
        : prev.nodeCardPreferredHeightPx

    const groupZInspectorBase =
      next.groupZInspectorBase != null && Number.isFinite(next.groupZInspectorBase) && next.groupZInspectorBase >= 0
        ? next.groupZInspectorBase
        : prev.groupZInspectorBase

    const groupZInteractBase =
      next.groupZInteractBase != null && Number.isFinite(next.groupZInteractBase) && next.groupZInteractBase >= 0
        ? next.groupZInteractBase
        : prev.groupZInteractBase

    if (
      clampPadPx === prev.clampPadPx &&
      dockedRightInsetPx === prev.dockedRightInsetPx &&
      dockedRightTopPx === prev.dockedRightTopPx &&
      anchorOffsetXPx === prev.anchorOffsetXPx &&
      anchorOffsetYPx === prev.anchorOffsetYPx &&
      cascadeStepPx === prev.cascadeStepPx &&
      interactPanelMinWidthPx === prev.interactPanelMinWidthPx &&
      interactPanelMinHeightPx === prev.interactPanelMinHeightPx &&
      interactPanelPreferredWidthTrustlinePx === prev.interactPanelPreferredWidthTrustlinePx &&
      interactPanelPreferredWidthPaymentPx === prev.interactPanelPreferredWidthPaymentPx &&
      interactPanelPreferredWidthWidePx === prev.interactPanelPreferredWidthWidePx &&
      interactPanelPreferredHeightLoadingPx === prev.interactPanelPreferredHeightLoadingPx &&
      interactPanelPreferredHeightConfirmPx === prev.interactPanelPreferredHeightConfirmPx &&
      interactPanelPreferredHeightPickingPx === prev.interactPanelPreferredHeightPickingPx &&
      edgeDetailMinWidthPx === prev.edgeDetailMinWidthPx &&
      edgeDetailMinHeightPx === prev.edgeDetailMinHeightPx &&
      edgeDetailPreferredWidthPx === prev.edgeDetailPreferredWidthPx &&
      edgeDetailPreferredHeightPx === prev.edgeDetailPreferredHeightPx &&
      nodeCardMinWidthPx === prev.nodeCardMinWidthPx &&
      nodeCardMinHeightPx === prev.nodeCardMinHeightPx &&
      nodeCardPreferredWidthPx === prev.nodeCardPreferredWidthPx &&
      nodeCardPreferredHeightPx === prev.nodeCardPreferredHeightPx &&
      groupZInspectorBase === prev.groupZInspectorBase &&
      groupZInteractBase === prev.groupZInteractBase
    ) {
      return
    }

    geometry.value = {
      clampPadPx,
      dockedRightInsetPx,
      dockedRightTopPx,

      anchorOffsetXPx,
      anchorOffsetYPx,
      cascadeStepPx,

      interactPanelMinWidthPx,
      interactPanelMinHeightPx,
      interactPanelPreferredWidthTrustlinePx,
      interactPanelPreferredWidthPaymentPx,
      interactPanelPreferredWidthWidePx,
      interactPanelPreferredHeightLoadingPx,
      interactPanelPreferredHeightConfirmPx,
      interactPanelPreferredHeightPickingPx,

      edgeDetailMinWidthPx,
      edgeDetailMinHeightPx,
      edgeDetailPreferredWidthPx,
      edgeDetailPreferredHeightPx,

      nodeCardMinWidthPx,
      nodeCardMinHeightPx,
      nodeCardPreferredWidthPx,
      nodeCardPreferredHeightPx,

      groupZInspectorBase,
      groupZInteractBase,
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
    const maybe = el as Element & { isConnected?: boolean }
    return typeof maybe.isConnected === 'boolean' ? maybe.isConnected : document.contains(el)
  }

  function isElementDisabled(el: Element): boolean {
    // Covers HTML form controls; other elements simply won't have `disabled`.
    const maybe = el as Element & { disabled?: unknown }
    return typeof maybe.disabled === 'boolean' ? maybe.disabled : false
  }

  function tryFocus(el: Element | null): boolean {
    if (!el) return false
    if (!isElementConnected(el)) return false
    if (isElementDisabled(el)) return false

    const focusFn = (el as Element & { focus?: unknown }).focus
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

  function restoreFocusAfterClose(o: { winId: number; reason: WindowCloseReason }): void {
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
    return (win.policy.group === 'interact' ? geometry.value.groupZInteractBase : geometry.value.groupZInspectorBase) + win.z
  }

  function closeGroupExcept(g: WindowGroup, exceptId: number, reason: WindowCloseReason): void {
    const ids: number[] = []
    for (const [id, win] of windowsMap) {
      if (id === exceptId) continue
      if (win.state === 'closing') continue
      if (win.policy.group === g) ids.push(id)
    }
    for (const id of ids) close(id, reason)
  }

  // NOTE: keep `type` and `data` decoupled at the signature level.
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
          sizingMode: 'fixed-width-auto-height',
          widthOwner: 'policy',
          heightOwner: 'measured',
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
          sizingMode: 'bounded-intrinsic',
          widthOwner: 'measured',
          heightOwner: 'measured',
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
          sizingMode: 'fixed-width-auto-height',
          widthOwner: 'policy',
          heightOwner: 'measured',
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
    const g = geometry.value
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
          if (p.includes('loading') || p.endsWith('-running') || p.endsWith('-preview')) return g.interactPanelPreferredHeightLoadingPx
          // Confirm steps are typically more compact than picking lists.
          if (p.startsWith('confirm-')) return g.interactPanelPreferredHeightConfirmPx
          // Picking steps are the default interact height.
          if (p.startsWith('picking-')) return g.interactPanelPreferredHeightPickingPx
          // Unknown/custom phases: keep legacy value to avoid changing behavior broadly.
          return g.interactPanelPreferredHeightPickingPx
        })()

        // Audit fix C-1: trustline preferredWidth MUST be 380.
        // Payment now has its own policy width; clearing stays on the wide contract.
        const preferredWidth =
          d.panel === 'trustline'
            ? g.interactPanelPreferredWidthTrustlinePx
            : d.panel === 'payment'
              ? g.interactPanelPreferredWidthPaymentPx
              : g.interactPanelPreferredWidthWidePx

        return {
          minWidth: g.interactPanelMinWidthPx,
          minHeight: g.interactPanelMinHeightPx,
          maxWidth: MAX,
          maxHeight: MAX,
          preferredWidth,
          preferredHeight,
        }
      }
      case 'edge-detail':
        return {
          minWidth: g.edgeDetailMinWidthPx,
          minHeight: g.edgeDetailMinHeightPx,
          maxWidth: MAX,
          maxHeight: MAX,
          preferredWidth: g.edgeDetailPreferredWidthPx,
          preferredHeight: g.edgeDetailPreferredHeightPx,
        }
      case 'node-card':
        return {
          minWidth: g.nodeCardMinWidthPx,
          minHeight: g.nodeCardMinHeightPx,
          maxWidth: MAX,
          maxHeight: MAX,
          preferredWidth: g.nodeCardPreferredWidthPx,
          preferredHeight: g.nodeCardPreferredHeightPx,
        }
      default: {
        const _exhaustive: never = type
        return _exhaustive
      }
    }
  }

  function syncRectToSizingPolicy(win: WindowInstance): void {
    const estimated = estimateSizeFromConstraints(win.constraints)

    if (!win.measured) {
      win.rect.width = estimated.width
      win.rect.height = estimated.height
      return
    }

    if (win.policy.widthOwner === 'policy') {
      win.rect.width = estimated.width
    }

    if (win.policy.heightOwner === 'policy') {
      win.rect.height = estimated.height
    }
  }

  function setActive(next: number | null): void {
    const resolved = next != null && isWindowActiveEligible(windowsMap.get(next) ?? { state: 'closing', lifecyclePhase: 'closing' })
      ? next
      : null

    activeId.value = resolved
    for (const [, win] of windowsMap) win.active = resolved != null && isWindowActiveEligible(win) && win.id === resolved
  }

  function focus(id: number): void {
    const win = windowsMap.get(id)
    if (!win) return
    if (!isWindowActiveEligible(win)) return
    focusCounter.value += 1
    win.z = focusCounter.value
    win.effectiveZ = computeEffectiveZ(win)
    setActive(id)
  }

  function setViewport(vp: { width: number; height: number }): void {
    const width = Number.isFinite(vp.width) && vp.width > 0 ? vp.width : DEFAULT_VIEWPORT_FALLBACK_WIDTH_PX
    const height = Number.isFinite(vp.height) && vp.height > 0 ? vp.height : DEFAULT_VIEWPORT_FALLBACK_HEIGHT_PX

    if (width !== vp.width || height !== vp.height) {
      warnOverlayDiagnostics('broken-clamp', 'Invalid viewport geometry would break WindowManager clamp; using safe fallback viewport.', {
        viewport: vp,
        fallbackViewport: { width, height },
      })
    }

    if (viewport.value.width === width && viewport.value.height === height) return
    viewport.value = { width, height }
  }

  function updateMeasuredSize(id: number, size: { width: number; height: number }): void {
    const win = windowsMap.get(id)
    if (!win) return
    if (win.state === 'closing') return

    // Safety: ignore invalid measurements (e.g. 0x0 in tests or during initial mount)
    // to avoid collapsing `rect` in reclamp().
    if (!(size.width > 0) || !(size.height > 0)) {
      warnOverlayDiagnostics('invalid-measured-size', 'Ignoring invalid window measurement for WindowManager.', {
        windowId: id,
        windowType: win.type,
        size,
      })
      return
    }

    // PERF-2: skip state updates if measurement is unchanged.
    // NOTE: we still allow the first measurement to set `measured` from null.
    const prev = win.measured
    if (prev && prev.width === size.width && prev.height === size.height) return

    win.lifecyclePhase = 'measuring'
    win.measured = { width: size.width, height: size.height }
  }

  function reclamp(id: number): void {
    const win = windowsMap.get(id)
    if (!win) return
    if (win.state === 'closing') return
    const vp = viewport.value
    const pad = geometry.value.clampPadPx
    const topBound = win.placement === 'docked-right' ? Math.max(pad, geometry.value.dockedRightTopPx) : pad
    const estimated = estimateSizeFromConstraints(win.constraints)

    const before = {
      left: win.rect.left,
      top: win.rect.top,
      width: win.rect.width,
      height: win.rect.height,
    }

    const measured = win.measured
    const resolvedWidth = measured && win.policy.widthOwner === 'measured' ? measured.width : estimated.width
    const resolvedHeight = measured && win.policy.heightOwner === 'measured' ? measured.height : estimated.height
    const w = resolvedWidth
    const h = resolvedHeight

    let nextLeft = before.left
    let nextTop = before.top
    let nextWidth = resolvedWidth
    let nextHeight = resolvedHeight

    // If pinned to anchor, recalculate position from anchor first.
    // IMPORTANT: once the window has been measured (exists in DOM), do NOT overwrite
    // the current rect on reclamp() — this would undo user drags and also breaks
    // singleton='reuse' expectations.
    if (win.anchor && win.placement === 'anchored' && !measured) {
      const dx = win.anchorOffset?.x ?? geometry.value.anchorOffsetXPx
      const dy = win.anchorOffset?.y ?? geometry.value.anchorOffsetYPx
      nextLeft = win.anchor.x + dx
      nextTop = win.anchor.y + dy
    }

    const maxLeft = Math.max(pad, vp.width - w - pad)
    const maxTop = Math.max(topBound, vp.height - h - pad)

    if (!measured) {
      // Initial placement (estimated size): establish a clean snap-aligned position.
      nextLeft = clamp(nextLeft, pad, maxLeft)
      nextTop = clamp(nextTop, topBound, maxTop)

      // Snap to 8px grid.
      nextLeft = snap8(nextLeft)
      nextTop = snap8(nextTop)

      // Re-clamp after snapping (important when viewport < window size).
      nextLeft = clamp(nextLeft, pad, maxLeft)
      nextTop = clamp(nextTop, topBound, maxTop)
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
      const clampedTop = clamp(nextTop, topBound, maxTop)

      const didClamp = clampedLeft !== nextLeft || clampedTop !== nextTop

      if (!didClamp) {
        // Strategy C: keep exact coordinates when already in bounds.
      } else {
        // Snap-on-clamp: if we had to push the window into bounds, align to 8px grid
        // while staying within [pad, max] constraints.
        nextLeft = snap8InRange(clampedLeft, pad, maxLeft)
        nextTop = snap8InRange(clampedTop, topBound, maxTop)
      }
    }

    // PERF-2: avoid no-op reactive writes (skip commit) when geometry is unchanged.
    if (
      nextLeft === before.left &&
      nextTop === before.top &&
      nextWidth === before.width &&
      nextHeight === before.height
    ) {
      if (win.measured && win.lifecyclePhase !== 'stable') win.lifecyclePhase = 'stable'
      return
    }

    if (nextLeft !== before.left) win.rect.left = nextLeft
    if (nextTop !== before.top) win.rect.top = nextTop
    if (nextWidth !== before.width) win.rect.width = nextWidth
    if (nextHeight !== before.height) win.rect.height = nextHeight
    if (win.measured) win.lifecyclePhase = 'stable'
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

  function close(id: number, _reason: WindowCloseReason): void {
    const win = windowsMap.get(id)
    if (!win) return
    // P1-3: already in closing — don't process twice.
    if (win.state === 'closing') return

    win.state = 'closing'
    win.lifecyclePhase = 'closing'

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

  function closeGroup(g: WindowGroup, reason: WindowCloseReason): void {
    const ids: number[] = []
    for (const [id, win] of windowsMap) {
      if (win.state === 'closing') continue
      if (win.policy.group === g) ids.push(id)
    }
    for (const id of ids) close(id, reason)
  }

  function closeByType(type: WindowType, reason: WindowCloseReason): number {
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

    win.data = data
    win.anchor = anchor
    win.constraints = constraints
    win.policy = policy
    // Policy may change group; update visual stacking base accordingly.
    win.effectiveZ = computeEffectiveZ(win)
    win.placement = anchor ? 'anchored' : 'docked-right'

    syncRectToSizingPolicy(win)

    // Re-position baseline only when necessary.
    if (anchorChanged || !win.measured) {
      if (win.placement === 'anchored' && win.anchor) {
        win.anchorOffset = { x: geometry.value.anchorOffsetXPx, y: geometry.value.anchorOffsetYPx }
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
      const next = cascadeShiftAvoidOverlaps({ rect: win.rect, others, step: geometry.value.cascadeStepPx })
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

  function open(o: WindowOpenArgs): number {
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
        // IMPORTANT: use `o.type` for proper discriminated-union narrowing.
        if (o.type === 'node-card' && focusMode !== 'never') {
          pendingNodeCardReuse = {
            id,
            data: o.data,
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

        win.data = data
        win.anchor = anchor
        win.constraints = constraints
        win.policy = policy
        // Policy may change group; update visual stacking base accordingly.
        win.effectiveZ = computeEffectiveZ(win)
        win.placement = anchor ? 'anchored' : 'docked-right'

        syncRectToSizingPolicy(win)

        // Re-position baseline only when necessary.
        // Feedback: for singleton='reuse', do not override user's dragged position
        // if the window already exists (measured) and anchor did not change.
        if (anchorChanged || !win.measured) {
          if (win.placement === 'anchored' && win.anchor) {
            win.anchorOffset = { x: geometry.value.anchorOffsetXPx, y: geometry.value.anchorOffsetYPx }
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
          const next = cascadeShiftAvoidOverlaps({ rect: win.rect, others, step: geometry.value.cascadeStepPx })
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
    const anchorOffset =
      placement === 'anchored' && anchor
        ? { x: geometry.value.anchorOffsetXPx, y: geometry.value.anchorOffsetYPx }
        : null
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
      lifecyclePhase: 'mounting',
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
      data,
    }
    windowsMap.set(id, win)

    // UX-2: remember the focus initiator for later restore on close().
    pushFocusReturn(id, initiator)

    // Collision avoidance for the first-frame estimate rect.
    {
      const others = Array.from(windowsMap.values())
        .filter((w) => w.id !== id)
        .map((w) => w.rect)
      const next = cascadeShiftAvoidOverlaps({ rect: win.rect, others, step: geometry.value.cascadeStepPx })
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
      if (!isWindowActiveEligible(win)) continue
      if (win.policy.group === g && win.active) return win
    }

    // Fallback: max effectiveZ within the group (P1-3: skip closing).
    let top: WindowInstance | null = null
    for (const [, win] of windowsMap) {
      if (!isWindowActiveEligible(win)) continue
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
