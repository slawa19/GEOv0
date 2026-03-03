import type { ComputedRef } from 'vue'

export type WindowGroup = 'interact' | 'inspector'

/**
 * Controls whether `wm.open()` changes z-order / active flag when reusing an existing window.
 *
 * - `'auto'`   (default): focus only on creation; reuse without focus change.
 * - `'always'`: always focus (user-initiated: click, dblclick, button).
 * - `'never'`  : never focus (watcher-driven reactive updates: SSE, phase change, pan/zoom).
 */
export type FocusMode = 'auto' | 'always' | 'never'

export type WindowType = 'interact-panel' | 'node-card' | 'edge-detail'

export type WindowSizeConstraints = {
  minWidth: number
  minHeight: number
  /** В MVP можно использовать большие числа вместо Infinity (например, 100000). */
  maxWidth: number
  maxHeight: number
  preferredWidth?: number
  preferredHeight?: number
}

export type WindowRect = { left: number; top: number; width: number; height: number }

export type AnchorSpace = 'host'
export type WindowAnchor = { x: number; y: number; space: AnchorSpace; source: string }

export type WindowPolicy = {
  group: WindowGroup
  singleton: 'reuse' | 'replace'
  escBehavior: 'close' | 'back-then-close' | 'ignore'
  closeOnOutsideClick: boolean
  /** Callback для 'back-then-close': окно пытается «съесть» ESC (шаг назад).
   *  Возвращает 'consumed' если ESC обработан, 'pass' если нужно закрыть. */
  onEsc?: () => 'consumed' | 'pass'

  /** Notify owner about UI-close vs programmatic close.
   * Useful for bridging WM window state with legacy open flags (e.g. node-card).
   */
  onClose?: (reason: 'esc' | 'action' | 'programmatic') => void
}

export type InteractPanelData =
  | { panel: 'payment'; phase: string; onBack?: () => boolean; onClose?: () => void }
  | { panel: 'trustline'; phase: string; onBack?: () => boolean; onClose?: () => void }
  | { panel: 'clearing'; phase: string; onBack?: () => boolean; onClose?: () => void }

export type WindowDataByType = {
  'interact-panel': InteractPanelData
  'node-card': { nodeId: string; onClose?: (reason: 'esc' | 'action' | 'programmatic') => void }
  /**
   * EdgeDetail UI context MUST be window-scoped.
   *
   * Rationale: during keepAlive/suppressed scenarios (e.g. edge-detail kept open
   * while another interact flow runs), live InteractState.{fromPid,toPid,title}
   * may drift. `win.data` is the source of truth for what this window represents.
   */
  'edge-detail': {
    fromPid: string
    toPid: string
    /** Stable edge identifier for this window (best-effort if not provided). */
    edgeKey?: string
    /** Optional pre-rendered title for this window (best-effort; UI may recompute). */
    title?: string
    onClose?: (reason: 'esc' | 'action' | 'programmatic') => void
  }
}

export type WindowData<T extends WindowType = WindowType> = WindowDataByType[T]

export type WindowLifecycleState = 'open' | 'closing'

export type WindowInstance = {
  id: number
  type: WindowType
  /** P1-3: transition-aware close. 'closing' means leave-animation is in progress. */
  state: WindowLifecycleState
  policy: WindowPolicy
  anchor: WindowAnchor | null
  /**
   * Offset from anchor for initial auto-positioning.
   * Used only while `measured` is null (before window exists in DOM).
   */
  anchorOffset: { x: number; y: number } | null
  active: boolean
  /**
   * Z within the WM focus order (used for intra-group ordering).
   * NOTE: visual stacking is controlled by `effectiveZ` to support group priorities.
   */
  z: number
  /**
   * Visual stacking order (CSS z-index) with group priority applied.
   * Requirement: `interact` MUST always render above `inspector`, regardless of focus.
   */
  effectiveZ: number
  placement: 'docked-right' | 'anchored'
  rect: WindowRect
  /** Нормативный MVP источник ограничений размеров (для first-frame estimate и maxHeight/overflow). */
  constraints: WindowSizeConstraints
  measured: { width: number; height: number } | null
  data: WindowData
}

// --- Type guards (Audit tech debt T-9): allow safe narrowing by `win.type` without `as any`.

export function isInteractPanelWindow(
  win: WindowInstance,
): win is WindowInstance & { type: 'interact-panel'; data: WindowDataByType['interact-panel'] } {
  return win.type === 'interact-panel'
}

export function isNodeCardWindow(
  win: WindowInstance,
): win is WindowInstance & { type: 'node-card'; data: WindowDataByType['node-card'] } {
  return win.type === 'node-card'
}

export type WindowManagerApi = {
  windows: ComputedRef<WindowInstance[]>

  /**
   * Return the current topmost window in a group using WM z-order / active.
   *
   * IMPORTANT: `active` is first-class in the WM spec; however, for robustness
   * we fall back to max-z selection inside the group.
   */
  getTopmostInGroup: (g: WindowGroup) => WindowInstance | null

  open: <T extends WindowType>(o: {
    type: T
    anchor?: WindowAnchor | null
    data: WindowData<T>
    /** @see FocusMode */
    focus?: FocusMode
  }) => number
  close: (id: number, reason: 'esc' | 'action' | 'programmatic') => void
  /**
   * P1-3: Finalize the removal of a closing window from the map.
   * Called from TransitionGroup @after-leave (or, as a fallback, by the safety timer).
   */
  finishClose: (id: number) => void
  /** Закрыть все окна данного типа (convenience helper). */
  closeByType: (type: WindowType, reason: 'esc' | 'action' | 'programmatic') => number
  /** Закрыть все окна группы. */
  closeGroup: (g: WindowGroup, reason: 'esc' | 'action' | 'programmatic') => void
  focus: (id: number) => void
  setViewport: (vp: { width: number; height: number }) => void
  updateMeasuredSize: (id: number, size: { width: number; height: number }) => void
  reclamp: (id: number) => void
  reclampAll: () => void
  handleEsc: (
    ev: KeyboardEvent,
    o: {
      isFormLikeTarget: (t: EventTarget | null) => boolean
      dispatchWindowEsc: () => boolean
    },
  ) => boolean
}

