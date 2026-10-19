import { computed, reactive, watch, type ComputedRef, type Reactive, type Ref } from 'vue'

import type { GraphLink, GraphSnapshot } from '../../types'
import { keyEdge, parseEdgeKey } from '../../utils/edgeKey'
import { isActiveStatus } from '../../utils/status'
import type { SimulatorActionClearingRealResponse, TrustlineInfo } from '../../api/simulatorTypes'
import type { ShownError } from '../../utils/errorMessage'
import { uiLocaleRef } from '../../i18n/locale'

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
  /** Latched: true when the current flow was initiated with pre-filled FROM (NodeCard/EdgeDetail/etc). */
  initiatedWithPrefilledFrom: boolean
  selectedEdgeKey: string | null
  /** Screen-space anchor for edge detail popup (host-relative coordinates). */
  edgeAnchor: { x: number; y: number } | null
  /** The sentence of the window's failure (048 S3: the sentence only - the request id is `errorRef`, never glued in). */
  error: string | null
  /**
   * 048 S3 (decision 3, AGENTS.md section 12): the server's correlation id of the failure in `error` - the window's error
   * block shows it as a muted second line and as `data-request-id`; `errorDetail` is the technical text (the HTTP
   * status, the code) for the block's `title`. Written together with `error` (`setError`) and cleared with it.
   */
  errorRef?: string | null
  errorDetail?: string | null

  /**
   * Last clearing action response. In `clearing-preview` (037 C: "the answer is awaited, then the result is shown") it is `null`
   * until the answer arrives and then the result the panel shows until Close; cleared when the NEXT clearing starts.
   *
   * `lastClearing` is intentionally preserved in idle state — it is used to
   * display the history of the last clearing cycle in BottomBar / HistoryLog.
   * Must NOT be reset in `resetToIdle()`.
   */
  lastClearing: SimulatorActionClearingRealResponse | null

  /**
   * 037 C: the text of a clearing that did not succeed (a refusal, or no usable answer), kept as a state of the panel: the error
   * toast that shows it for a few seconds is cleared by the application and must not take the explanation with it. Set when the
   * clearing fails, cleared when the next clearing starts and when the panel is closed (`resetToIdle`).
   * 048 S3: the shown form - the sentence, the request id apart from it, the technical detail. (The error toast is gone
   * since 048 S3; the failure stays a state of the panel because `state.error` is cleared by the next step of a flow.)
   */
  clearingFailure?: ShownError | null
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
  isCanvasNodePickPhase: ComputedRef<boolean>

  clearError: () => void
  /** 048 S3: the window's failure - the sentence, its request id and its detail, written together. */
  setError: (shown: ShownError | null) => void
  resetToIdle: () => void

  startPaymentFlow: () => void
  /** Atomically start payment flow with pre-filled FROM (skips picking-payment-from phase). */
  startPaymentFlowWithFrom: (fromPid: string) => void
  startTrustlineFlow: () => void
  /** Atomically start trustline flow with pre-filled FROM (skips picking-trustline-from phase). */
  startTrustlineFlowWithFrom: (fromPid: string) => void
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
  setClearingFailure: (failure: ShownError | null) => void
} {
  const state = reactive<InteractState>({
    phase: 'idle',
    fromPid: null,
    toPid: null,
    initiatedWithPrefilledFrom: false,
    selectedEdgeKey: null,
    edgeAnchor: null,
    error: null,
    errorRef: null,
    errorDetail: null,

    lastClearing: null,
    clearingFailure: null,
  })

  const phase = computed(() => state.phase)
  const isPickingPhase = computed(() => state.phase.startsWith('picking-'))
  const isCanvasNodePickPhase = computed(() => {
    return (
      state.phase.startsWith('picking-') ||
      state.phase === 'confirm-payment' ||
      state.phase === 'editing-trustline' ||
      state.phase === 'confirm-trustline-create'
    )
  })

  function clearError() {
    setError(null)
  }

  // 046 wave 3 (BACKLOG 046-3): the failure as it was shown, kept so its sentence is drawn again in the language of a switch
  // (`shownErrorIn`: `text` renders in the language of now; a plain `ShownError` keeps its text). `state.error` stays the
  // one string every window reads.
  // A text written into `state.error` by anything else drops the kept failure at once (a synchronous watcher), so a stale
  // one is never drawn over it.
  let shownNow: ShownError | null = null
  let writingShown = false
  function writeShownText(text: string | null) {
    writingShown = true
    try {
      state.error = text
    } finally {
      writingShown = false
    }
  }
  watch(() => state.error, () => {
    if (!writingShown) shownNow = null
  }, { flush: 'sync' })
  watch(uiLocaleRef, () => {
    if (shownNow) writeShownText(shownNow.text)
  }, { flush: 'sync' })

  function setError(shown: ShownError | null) {
    shownNow = shown
    writeShownText(shown ? shown.text : null)
    state.errorRef = shown ? shown.ref : null
    state.errorDetail = shown ? shown.detail : null
  }

  /**
   * Internal single source of truth for initializing all flow-state fields.
   * All public `start*` functions MUST route through this to avoid partial-init bugs
   * when new fields are added to InteractState.
   */
  function _initFlowState(
    phase: InteractPhase,
    fromPid: string | null = null,
    initiatedWithPrefilledFrom: boolean = false,
  ) {
    clearError()
    state.phase = phase
    state.fromPid = fromPid
    state.toPid = null
    state.initiatedWithPrefilledFrom = initiatedWithPrefilledFrom
    state.selectedEdgeKey = null
    state.edgeAnchor = null
  }

  function resetToIdle() {
    state.phase = 'idle'
    state.fromPid = null
    state.toPid = null
    state.initiatedWithPrefilledFrom = false
    state.selectedEdgeKey = null
    state.edgeAnchor = null
    clearError()
    // lastClearing intentionally preserved — see InteractState.lastClearing JSDoc.
    state.clearingFailure = null
  }

  function startPaymentFlow() {
    _initFlowState('picking-payment-from')
  }

  /**
   * Atomically start payment flow with pre-filled FROM (skips picking-payment-from phase).
   * Guards: if `fromPidRaw` is empty/whitespace after trim, falls back to `startPaymentFlow()`
   * (no pre-fill, `initiatedWithPrefilledFrom = false`).
   */
  function startPaymentFlowWithFrom(fromPidRaw: string) {
    const fromPid = fromPidRaw.trim()
    if (!fromPid) {
      // Invalid/empty fromPid: fall back to normal flow without pre-fill.
      startPaymentFlow()
      return
    }
    _initFlowState('picking-payment-to', fromPid, true)
  }

  function startTrustlineFlow() {
    _initFlowState('picking-trustline-from')
  }

  /**
   * Atomically start trustline flow with pre-filled FROM (skips picking-trustline-from phase).
   * Guards: if `fromPidRaw` is empty/whitespace after trim, falls back to `startTrustlineFlow()`
   * (no pre-fill, `initiatedWithPrefilledFrom = false`).
   */
  function startTrustlineFlowWithFrom(fromPidRaw: string) {
    const fromPid = fromPidRaw.trim()
    if (!fromPid) {
      // Invalid/empty fromPid: fall back to normal flow without pre-fill.
      startTrustlineFlow()
      return
    }
    _initFlowState('picking-trustline-to', fromPid, true)
  }

  function startClearingFlow() {
    _initFlowState('confirm-clearing')
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
    const id = nodeId.trim()
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

    if (state.phase === 'confirm-payment') {
      clearError()
      if (id !== state.fromPid) {
        state.toPid = id
      } else {
        state.toPid = null
        state.phase = 'picking-payment-to'
      }
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
      return
    }

    if (state.phase === 'editing-trustline' || state.phase === 'confirm-trustline-create') {
      clearError()
      if (id !== state.fromPid) {
        state.toPid = id
        recomputeTrustlinePhase()
      } else {
        state.toPid = null
        state.phase = 'picking-trustline-to'
        state.selectedEdgeKey = null
        state.edgeAnchor = null
      }
      return
    }
  }

  function selectEdge(edgeKey: string, anchor: { x: number; y: number } | null = null) {
    const parsed = parseEdgeKey(edgeKey)
    if (!parsed) return
    clearError()
    state.fromPid = parsed.from
    state.toPid = parsed.to
    state.initiatedWithPrefilledFrom = true
    state.selectedEdgeKey = keyEdge(parsed.from, parsed.to)
    state.edgeAnchor = anchor
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
      // 045 S3 (BACKLOG 045-1): the create step shows From. A cleared sender goes back to choosing it, and the recipient
      // chosen as the sender is dropped - `recomputeTrustlinePhase` does nothing for a pair of one participant, which
      // left the create step open for a line from a participant to itself.
      if (!v || v === state.toPid) {
        state.fromPid = v
        state.toPid = null
        state.selectedEdgeKey = null
        state.edgeAnchor = null
        state.phase = v ? 'picking-trustline-to' : 'picking-trustline-from'
        return
      }
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
    state.fromPid = fromPid.trim() || null
    state.toPid = toPid.trim() || null
    if (!state.fromPid || !state.toPid) return
    state.initiatedWithPrefilledFrom = true
    state.selectedEdgeKey = keyEdge(state.fromPid, state.toPid)
    state.edgeAnchor = null
    state.phase = 'editing-trustline'
  }

  function enterClearingPreview() {
    clearError()
    state.phase = 'clearing-preview'
    state.lastClearing = null
    state.clearingFailure = null
  }

  function setLastClearing(r: SimulatorActionClearingRealResponse | null) {
    state.lastClearing = r
  }

  function setClearingFailure(failure: ShownError | null) {
    state.clearingFailure = failure
  }

  return {
    state,
    phase,
    isPickingPhase,
    isCanvasNodePickPhase,

    clearError,
    setError,
    resetToIdle,

    startPaymentFlow,
    startPaymentFlowWithFrom,
    startTrustlineFlow,
    startTrustlineFlowWithFrom,
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
    setClearingFailure,
  }
}
