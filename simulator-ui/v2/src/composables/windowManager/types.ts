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

export type WindowCloseReason = 'esc' | 'action' | 'outside-click' | 'programmatic'

export type WindowType = 'interact-panel' | 'node-card' | 'edge-detail'

export type WindowSizingMode = 'fixed-width-auto-height' | 'bounded-intrinsic'

export type WindowAxisOwner = 'policy' | 'measured'

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

export type WindowManagerGeometryPx = {
  /** Minimum padding from viewport edges for reclamp() bounds. */
  clampPadPx: number
  /** Default top offset for docked-right windows (HUD stack height). */
  dockedRightTopPx: number
  /** Default right inset for docked-right windows. */
  dockedRightInsetPx: number

  /** Default dx for anchored placement before first measure. */
  anchorOffsetXPx: number
  /** Default dy for anchored placement before first measure. */
  anchorOffsetYPx: number
  /** Default cascade step when shifting windows to avoid overlaps. */
  cascadeStepPx: number

  /** Interact panel constraints (must stay aligned with overlay CSS). */
  interactPanelMinWidthPx: number
  interactPanelMinHeightPx: number
  interactPanelPreferredWidthTrustlinePx: number
  interactPanelPreferredWidthPaymentPx: number
  interactPanelPreferredWidthWidePx: number
  interactPanelPreferredHeightLoadingPx: number
  interactPanelPreferredHeightConfirmPx: number
  interactPanelPreferredHeightPickingPx: number

  /** Inspector: edge detail constraints. */
  edgeDetailMinWidthPx: number
  edgeDetailMinHeightPx: number
  edgeDetailPreferredWidthPx: number
  edgeDetailPreferredHeightPx: number

  /** Inspector: node card constraints. */
  nodeCardMinWidthPx: number
  nodeCardMinHeightPx: number
  nodeCardPreferredWidthPx: number
  nodeCardPreferredHeightPx: number

  /** WM visual stacking bases for group priority. */
  groupZInspectorBase: number
  groupZInteractBase: number
}

export type AnchorSpace = 'host'
export type WindowAnchor = { x: number; y: number; space: AnchorSpace; source: string }

export type WindowPolicy = {
  group: WindowGroup
  singleton: 'reuse' | 'replace'
  sizingMode: WindowSizingMode
  widthOwner: WindowAxisOwner
  heightOwner: WindowAxisOwner
  escBehavior: 'close' | 'back-then-close' | 'ignore'
  closeOnOutsideClick: boolean
  /** Callback для 'back-then-close': окно пытается «съесть» ESC (шаг назад).
   *  Возвращает 'consumed' если ESC обработан, 'pass' если нужно закрыть. */
  onEsc?: () => 'consumed' | 'pass'

  /** Notify owner about UI-close vs programmatic close.
   * Useful for bridging WM window state with legacy open flags (e.g. node-card).
   */
  onClose?: (reason: WindowCloseReason) => void
}

export type InteractPanelData =
  | { panel: 'payment'; phase: string; onBack?: () => boolean; onClose?: () => void }
  | { panel: 'trustline'; phase: string; onBack?: () => boolean; onClose?: () => void }
  | { panel: 'clearing'; phase: string; onBack?: () => boolean; onClose?: () => void }

export type WindowDataByType = {
  'interact-panel': InteractPanelData
  'node-card': { nodeId: string; onClose?: (reason: WindowCloseReason) => void }
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
    onClose?: (reason: WindowCloseReason) => void
  }
}

export type WindowData<T extends WindowType = WindowType> = WindowDataByType[T]

export type WindowOpenArgs =
  | {
      type: 'interact-panel'
      anchor?: WindowAnchor | null
      data: WindowDataByType['interact-panel']
      /** @see FocusMode */
      focus?: FocusMode
    }
  | {
      type: 'node-card'
      anchor?: WindowAnchor | null
      data: WindowDataByType['node-card']
      /** @see FocusMode */
      focus?: FocusMode
    }
  | {
      type: 'edge-detail'
      anchor?: WindowAnchor | null
      data: WindowDataByType['edge-detail']
      /** @see FocusMode */
      focus?: FocusMode
    }

export type WindowLifecycleState = 'open' | 'closing'

export type WindowLifecyclePhase = 'mounting' | 'measuring' | 'stable' | 'closing'

export function isWindowActiveEligible(win: Pick<WindowInstance, 'state' | 'lifecyclePhase'>): boolean {
  return win.state === 'open' && win.lifecyclePhase !== 'closing'
}

export type WindowInstance = {
  id: number
  type: WindowType
  /** P1-3: transition-aware close. 'closing' means leave-animation is in progress. */
  state: WindowLifecycleState
  /**
   * Explicit render/measurement lifecycle contract.
   * IMPORTANT: focus ownership is NOT encoded here; it remains orthogonal in `active`.
   */
  lifecyclePhase: WindowLifecyclePhase
  policy: WindowPolicy
  anchor: WindowAnchor | null
  /**
   * Offset from anchor for initial auto-positioning.
   * Used only while `measured` is null (before window exists in DOM).
   */
  anchorOffset: { x: number; y: number } | null
  /**
   * Focus-selection flag for WindowManager ordering.
   * Valid only for focus-eligible windows (`state==='open'` and `lifecyclePhase!=='closing'`).
   * Closing windows must never remain active.
   */
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

export function isEdgeDetailWindow(
  win: WindowInstance,
): win is WindowInstance & { type: 'edge-detail'; data: WindowDataByType['edge-detail'] } {
  return win.type === 'edge-detail'
}

export type WindowManagerApi = {
  windows: ComputedRef<WindowInstance[]>

  /** Update geometry tokens used by placement/reclamp. */
  setGeometry: (g: Partial<WindowManagerGeometryPx>) => void

  /**
   * Return the current topmost window in a group using WM z-order / active.
   *
   * IMPORTANT: `active` is first-class in the WM spec; however, for robustness
   * we fall back to max-z selection inside the group.
   */
  getTopmostInGroup: (g: WindowGroup) => WindowInstance | null

  open: (o: WindowOpenArgs) => number
  close: (id: number, reason: WindowCloseReason) => void
  /**
   * P1-3: Finalize the removal of a closing window from the map.
   * Called from TransitionGroup @after-leave (or, as a fallback, by the safety timer).
   */
  finishClose: (id: number) => void
  /** Закрыть все окна данного типа (convenience helper). */
  closeByType: (type: WindowType, reason: WindowCloseReason) => number
  /** Закрыть все окна группы. */
  closeGroup: (g: WindowGroup, reason: WindowCloseReason) => void
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

