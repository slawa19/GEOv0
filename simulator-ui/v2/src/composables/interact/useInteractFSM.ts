import { computed, reactive, type ComputedRef, type Reactive, type Ref } from 'vue'

import type { GraphLink, GraphSnapshot } from '../../types'
import { keyEdge, parseEdgeKey } from '../../utils/edgeKey'
import { isActiveStatus } from '../../utils/status'
import type { SimulatorActionClearingRealResponse, TrustlineInfo } from '../../api/simulatorTypes'

export type InteractPhase =
  | 'idle'
  | 'picking-payment-from'
  | 'picking-payment-to'
  | 'confirm-payment'
  | 'picking-trustline-from'
  | 'picking-trustline-to'
  | 'confirm-trustline-create'
  | 'editing-trustline'
  | 'confirm-clearing'
  | 'clearing-preview'
  | 'clearing-running'

export type InteractState = {
  phase: InteractPhase
  fromPid: string | null
  toPid: string | null
  selectedEdgeKey: string | null
  /** Screen-space anchor for edge detail popup (host-relative coordinates). */
  edgeAnchor: { x: number; y: number } | null
  error: string | null

  /**
   * Last clearing action response (populated in `clearing-preview`).
   *
   * `lastClearing` is intentionally preserved in idle state — it is used to
   * display the history of the last clearing cycle in BottomBar / HistoryLog.
   * Must NOT be reset in `resetToIdle()`.
   */
  lastClearing: SimulatorActionClearingRealResponse | null
}

function findActiveLink(snapshot: GraphSnapshot | null, from: string | null, to: string | null): GraphLink | null {
  if (!snapshot || !from || !to) return null
  for (const l of snapshot.links ?? []) {
    if (l.source === from && l.target === to && isActiveStatus(l.status)) return l
  }
  return null
}

export function useInteractFSM(opts: {
  snapshot: Ref<GraphSnapshot | null>
  findActiveTrustline: (from: string | null, to: string | null) => TrustlineInfo | null
  onNodeClick?: (nodeId: string) => void
}): {
  state: Reactive<InteractState>
  phase: ComputedRef<InteractPhase>
  isPickingPhase: ComputedRef<boolean>

  clearError: () => void
  resetToIdle: () => void

  startPaymentFlow: () => void
  startTrustlineFlow: () => void
  startClearingFlow: () => void

  selectNode: (nodeId: string) => void
  selectEdge: (edgeKey: string, anchor?: { x: number; y: number } | null) => void
  selectTrustline: (fromPid: string, toPid: string) => void

  // Dropdown helpers
  setPaymentFromPid: (pid: string | null) => void
  setPaymentToPid: (pid: string | null) => void
  setTrustlineFromPid: (pid: string | null) => void
  setTrustlineToPid: (pid: string | null) => void

  // Clearing helpers
  enterClearingPreview: () => void
  setLastClearing: (r: SimulatorActionClearingRealResponse | null) => void
  enterClearingRunning: () => void
} {
  const state = reactive<InteractState>({
    phase: 'idle',
    fromPid: null,
    toPid: null,
    selectedEdgeKey: null,
    edgeAnchor: null,
    error: null,

    lastClearing: null,
  })

  const phase = computed(() => state.phase)
  const isPickingPhase = computed(() => String(state.phase ?? '').startsWith('picking-'))

  function clearError() {
    state.error = null
  }

  function resetToIdle() {
    state.phase = 'idle'
    state.fromPid = null
    state.toPid = null
    state.selectedEdgeKey = null
    state.edgeAnchor = null
    state.error = null
    // lastClearing intentionally preserved — see InteractState.lastClearing JSDoc.
  }

  function startNewFlow(p: InteractPhase) {
    clearError()
    state.phase = p
    state.fromPid = null
    state.toPid = null
    state.selectedEdgeKey = null
    state.edgeAnchor = null
  }

  function startPaymentFlow() {
    startNewFlow('picking-payment-from')
  }

  function startTrustlineFlow() {
    startNewFlow('picking-trustline-from')
  }

  function startClearingFlow() {
    clearError()
    state.phase = 'confirm-clearing'
    state.fromPid = null
    state.toPid = null
    state.selectedEdgeKey = null
    state.edgeAnchor = null

    // Clear stale results so the panel doesn't flash previous cycles.
    state.lastClearing = null
  }

  function recomputeTrustlinePhase() {
    if (!state.fromPid || !state.toPid) return
    if (state.fromPid === state.toPid) return

    // Prefer fetched trustlines list when present.
    const tl = opts.findActiveTrustline(state.fromPid, state.toPid)
    const snapLink = findActiveLink(opts.snapshot.value, state.fromPid, state.toPid)
    const has = !!tl || !!snapLink

    if (has) {
      state.selectedEdgeKey = keyEdge(state.fromPid, state.toPid)
      state.edgeAnchor = null
      state.phase = 'editing-trustline'
    } else {
      state.selectedEdgeKey = null
      state.edgeAnchor = null
      state.phase = 'confirm-trustline-create'
    }
  }

  function selectNode(nodeId: string) {
    const id = String(nodeId ?? '').trim()
    if (!id) return

    opts.onNodeClick?.(id)

    if (state.phase === 'picking-payment-from') {
      clearError()
      state.fromPid = id
      state.toPid = null
      state.phase = 'picking-payment-to'
      return
    }

    if (state.phase === 'picking-payment-to') {
      clearError()
      state.toPid = id
      state.phase = 'confirm-payment'
      return
    }

    if (state.phase === 'picking-trustline-from') {
      clearError()
      state.fromPid = id
      state.toPid = null
      state.phase = 'picking-trustline-to'
      return
    }

    if (state.phase === 'picking-trustline-to') {
      clearError()
      state.toPid = id
      recomputeTrustlinePhase()
    }
  }

  function selectEdge(edgeKey: string, anchor?: { x: number; y: number } | null) {
    const parsed = parseEdgeKey(edgeKey)
    if (!parsed) return
    clearError()
    state.fromPid = parsed.from
    state.toPid = parsed.to
    state.selectedEdgeKey = keyEdge(parsed.from, parsed.to)
    state.edgeAnchor = anchor ?? null
    state.phase = 'editing-trustline'
  }

  function setPaymentFromPid(pid: string | null) {
    clearError()

    const v = pid ? String(pid).trim() || null : null

    if (state.phase === 'picking-payment-from') {
      state.fromPid = v
      state.toPid = null
      if (v) state.phase = 'picking-payment-to'
      return
    }

    if (state.phase === 'picking-payment-to') {
      // Allow changing From during To-step (UX).
      state.fromPid = v
      state.toPid = null
      state.phase = v ? 'picking-payment-to' : 'picking-payment-from'
      return
    }

    if (state.phase === 'confirm-payment') {
      state.fromPid = v
      if (!v) {
        state.toPid = null
        state.phase = 'picking-payment-from'
      }
    }
  }

  function setPaymentToPid(pid: string | null) {
    clearError()

    const v = pid ? String(pid).trim() || null : null

    if (state.phase === 'picking-payment-to') {
      state.toPid = v
      if (v) state.phase = 'confirm-payment'
      return
    }

    if (state.phase === 'confirm-payment') {
      state.toPid = v
      if (!v) state.phase = 'picking-payment-to'
    }
  }

  function setTrustlineFromPid(pid: string | null) {
    clearError()

    const v = pid ? String(pid).trim() || null : null

    if (state.phase === 'picking-trustline-from') {
      state.fromPid = v
      state.toPid = null
      if (v) state.phase = 'picking-trustline-to'
      return
    }

    if (state.phase === 'picking-trustline-to') {
      // Allow changing From during To-step.
      state.fromPid = v
      state.toPid = null
      state.phase = v ? 'picking-trustline-to' : 'picking-trustline-from'
      return
    }

    if (state.phase === 'editing-trustline' || state.phase === 'confirm-trustline-create') {
      state.fromPid = v
      recomputeTrustlinePhase()
    }
  }

  function setTrustlineToPid(pid: string | null) {
    clearError()

    const v = pid ? String(pid).trim() || null : null

    if (state.phase === 'picking-trustline-to') {
      state.toPid = v
      if (v) recomputeTrustlinePhase()
      return
    }

    if (state.phase === 'editing-trustline' || state.phase === 'confirm-trustline-create') {
      state.toPid = v
      if (!v) {
        state.phase = 'picking-trustline-to'
        state.selectedEdgeKey = null
        state.edgeAnchor = null
        return
      }
      recomputeTrustlinePhase()
    }
  }

  function selectTrustline(fromPid: string, toPid: string) {
    clearError()
    state.fromPid = String(fromPid ?? '').trim() || null
    state.toPid = String(toPid ?? '').trim() || null
    if (!state.fromPid || !state.toPid) return
    state.selectedEdgeKey = keyEdge(state.fromPid, state.toPid)
    state.edgeAnchor = null
    state.phase = 'editing-trustline'
  }

  function enterClearingPreview() {
    clearError()
    state.phase = 'clearing-preview'
    state.lastClearing = null
  }

  function setLastClearing(r: SimulatorActionClearingRealResponse | null) {
    state.lastClearing = r
  }

  function enterClearingRunning() {
    clearError()
    state.phase = 'clearing-running'
  }

  return {
    state,
    phase,
    isPickingPhase,

    clearError,
    resetToIdle,

    startPaymentFlow,
    startTrustlineFlow,
    startClearingFlow,

    selectNode,
    selectEdge,
    selectTrustline,

    setPaymentFromPid,
    setPaymentToPid,
    setTrustlineFromPid,
    setTrustlineToPid,

    enterClearingPreview,
    setLastClearing,
    enterClearingRunning,
  }
}
