import { ref, type Ref } from 'vue'

import type { GraphLink } from '../types'
import { keyEdge } from '../utils/edgeKey'

import type { WindowAnchor } from './windowManager/types'

export type EdgeDetailState = 'closed' | 'live' | 'suppressed' | 'keepAlive'

export type EdgeDetailCloseReason = 'action' | 'programmatic'
export type EdgeDetailFocusPolicy = 'always' | 'never'

export type EdgeDetailOpenRequest = {
  fromPid: string
  toPid: string
  anchor: WindowAnchor | null
  focus?: EdgeDetailFocusPolicy
  source?: 'auto' | 'manual'
}

export type EdgeDetailWmOpenArgs = {
  type: 'edge-detail'
  anchor: WindowAnchor | null
  data: {
    fromPid: string
    toPid: string
    edgeKey: string
    title: string
  }
  focus: EdgeDetailFocusPolicy
}

export type EdgeDetailWmPort = {
  open: (args: EdgeDetailWmOpenArgs) => number
  close: (id: number, reason: EdgeDetailCloseReason) => void
}

function anchorKey(a: WindowAnchor | null): string {
  if (!a) return ''
  return `${a.x}|${a.y}|${a.space}|${a.source}`
}

function selectionKey(req: EdgeDetailOpenRequest): string {
  // IMPORTANT: include anchor, since re-selecting the same edge often changes the anchor.
  // That should re-enable auto-open after a UI-close suppression.
  return `${req.fromPid}→${req.toPid}@${anchorKey(req.anchor)}`
}

/**
 * ARCH-6: Deterministic state machine for EdgeDetail WindowManager wiring.
 *
 * Goals:
 * - Replace ad-hoc flags in root with a single union-type state.
 * - No watchEffect inside this composable; all updates are explicit method calls.
 */
export function useWmEdgeDetail(): {
  state: Ref<EdgeDetailState>
  frozenLink: Ref<GraphLink | null>

  /** Feed the current auto (Interact FSM) desire to show EdgeDetail quick-info. */
  syncAuto: (req: EdgeDetailOpenRequest | null) => void

  /** User-initiated open/update (e.g. edge click outside of Interact quick-info). */
  open: (req: EdgeDetailOpenRequest) => void

  /**
   * UI-close: in auto mode it becomes "suppressed" (block auto-reopen until selection changes).
   * In manual mode it becomes "closed".
   */
  close: (opts?: { reason?: EdgeDetailCloseReason; suppress?: boolean }) => void

  /** Explicit suppression (e.g. outside-click / modal). */
  suppress: (reason?: string) => void

  /** KeepAlive: keep the window open as frozen context while Interact flow changes phase. */
  allowKeepAlive: (o?: { frozenLink?: GraphLink | null }) => void
  releaseKeepAlive: () => void

  /** Apply the desired open/close to WindowManager (idempotent). */
  applyToWindowManager: (wm: EdgeDetailWmPort, o?: { closeReason?: EdgeDetailCloseReason }) => void
} {
  const state = ref<EdgeDetailState>('closed')

  // NOTE: we keep driver separate from union-state; union-state stays minimal and UI-oriented.
  const driver = ref<'auto' | 'manual' | null>(null)

  // Auto request comes from Interact FSM (editing-trustline quick-info).
  const autoReq = ref<EdgeDetailOpenRequest | null>(null)

  // Manual request comes from user actions (edge click).
  const manualReq = ref<EdgeDetailOpenRequest | null>(null)

  // Suppression blocks auto-reopen for the same selection key.
  const suppressedKey = ref<string>('')

  // KeepAlive freezes the window content and link props.
  const keepAlive = ref(false)
  const frozenReq = ref<EdgeDetailOpenRequest | null>(null)
  const frozenLink = ref<GraphLink | null>(null)

  // WM bookkeeping (singleton window id + last applied open signature).
  const winId = ref<number | null>(null)
  const appliedOpenKey = ref<string>('')

  function buildOpenArgs(req: EdgeDetailOpenRequest): EdgeDetailWmOpenArgs {
    const fromPid = String(req.fromPid)
    const toPid = String(req.toPid)
    return {
      type: 'edge-detail',
      anchor: req.anchor ?? null,
      data: {
        fromPid,
        toPid,
        edgeKey: keyEdge(fromPid, toPid),
        title: `${fromPid} → ${toPid}`,
      },
      focus: req.focus ?? (req.source === 'manual' ? 'always' : 'never'),
    }
  }

  function desiredOpenReq(): EdgeDetailOpenRequest | null {
    if (state.value === 'keepAlive' && keepAlive.value && frozenReq.value) return frozenReq.value
    if (state.value !== 'live') return null
    if (driver.value === 'manual') return manualReq.value
    return autoReq.value
  }

  function transitionFromAuto(): void {
    // Manual driver is authoritative; auto should not close or replace it.
    if (driver.value === 'manual') return

    // keepAlive overrides auto; it persists until releaseKeepAlive().
    if (state.value === 'keepAlive' && keepAlive.value) return

    const req = autoReq.value
    if (!req) {
      // Leaving eligible auto context clears suppression.
      suppressedKey.value = ''
      if (state.value === 'suppressed') state.value = 'closed'

      if (driver.value === 'auto' && state.value === 'live') {
        state.value = 'closed'
        driver.value = null
      }
      return
    }

    driver.value = 'auto'

    // If auto-open is suppressed for the same selection, stay suppressed.
    if (suppressedKey.value && selectionKey(req) === suppressedKey.value) {
      state.value = 'suppressed'
      return
    }

    // Otherwise auto-open/update.
    state.value = 'live'
  }

  function syncAuto(req: EdgeDetailOpenRequest | null): void {
    autoReq.value = req

    // If selection changed, lift suppression deterministically.
    if (state.value === 'suppressed' && autoReq.value && suppressedKey.value) {
      if (selectionKey(autoReq.value) !== suppressedKey.value) suppressedKey.value = ''
    }

    transitionFromAuto()
  }

  function open(req: EdgeDetailOpenRequest): void {
    manualReq.value = { ...req, source: 'manual' }
    driver.value = 'manual'
    state.value = 'live'

    // Manual open always lifts auto suppression.
    suppressedKey.value = ''
  }

  function releaseKeepAlive(): void {
    keepAlive.value = false
    frozenReq.value = null
    frozenLink.value = null

    if (state.value === 'keepAlive') {
      state.value = 'closed'
      // After keepAlive ends, allow auto to take over again.
      driver.value = null
      transitionFromAuto()
    }
  }

  function allowKeepAlive(o?: { frozenLink?: GraphLink | null }): void {
    // Only meaningful while the window is live.
    const req = desiredOpenReq()
    if (!req) return

    keepAlive.value = true
    frozenReq.value = { ...req }
    frozenLink.value = o?.frozenLink ?? null
    state.value = 'keepAlive'
  }

  function suppress(_reason?: string): void {
    // Suppression implies keepAlive is cleared (matches existing root semantics).
    releaseKeepAlive()

    const req = autoReq.value
    if (req) suppressedKey.value = selectionKey(req)

    state.value = 'suppressed'
    driver.value = 'auto'
  }

  function close(opts?: { reason?: EdgeDetailCloseReason; suppress?: boolean }): void {
    // NOTE: `reason` is applied by applyToWindowManager() via its `closeReason` argument.
    // We keep the state machine pure and WM-agnostic here.
    void opts

    releaseKeepAlive()

    const shouldSuppress = opts?.suppress ?? driver.value === 'auto'
    if (shouldSuppress && autoReq.value) {
      suppressedKey.value = selectionKey(autoReq.value)
      state.value = 'suppressed'
      driver.value = 'auto'
      return
    }

    state.value = 'closed'
    driver.value = null
    manualReq.value = null
    suppressedKey.value = ''
  }

  function applyToWindowManager(wm: EdgeDetailWmPort, o?: { closeReason?: EdgeDetailCloseReason }): void {
    const req = desiredOpenReq()

    if (!req || state.value === 'closed' || state.value === 'suppressed') {
      if (winId.value != null) {
        wm.close(winId.value, o?.closeReason ?? 'programmatic')
        winId.value = null
        appliedOpenKey.value = ''
      }
      return
    }

    const openArgs = buildOpenArgs(req)
    const nextKey = `${openArgs.focus}|${openArgs.data.edgeKey}|${openArgs.data.fromPid}|${openArgs.data.toPid}|${anchorKey(openArgs.anchor)}`
    if (appliedOpenKey.value === nextKey && winId.value != null) return

    const id = wm.open(openArgs)
    winId.value = id
    appliedOpenKey.value = nextKey
  }

  return {
    state,
    frozenLink,
    syncAuto,
    open,
    close,
    suppress,
    allowKeepAlive,
    releaseKeepAlive,
    applyToWindowManager,
  }
}

