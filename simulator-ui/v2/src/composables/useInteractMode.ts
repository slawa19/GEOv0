import { computed, ref, type ComputedRef, type Reactive, type Ref } from 'vue'

import type { GraphSnapshot } from '../types'
import { parseAmountNumber, parseAmountStringOrNull } from '../utils/amount'
import { isActiveStatus } from '../utils/status'
import type { ParticipantInfo, SimulatorActionClearingRealResponse, TrustlineInfo } from '../api/simulatorTypes'
import { useInteractActions } from './useInteractActions'
import { useInteractDataCache } from './interact/useInteractDataCache'
import { useInteractFSM, type InteractPhase, type InteractState } from './interact/useInteractFSM'
import { useInteractHistory, type InteractHistoryEntry as InteractHistoryEntryT } from './interact/useInteractHistory'

/** BUG-5: Entry in the Interact Mode history log. */
export type InteractHistoryEntry = InteractHistoryEntryT

export type { InteractPhase, InteractState }

export function useInteractMode(opts: {
  actions: ReturnType<typeof useInteractActions>
  equivalent: Ref<string>
  snapshot: Ref<GraphSnapshot | null>
  onNodeClick?: (nodeId: string) => void
  /** BUG-3: called after successful clearing to trigger FX animation (gold pulse on cycle edges). */
  onClearingDone?: (result: SimulatorActionClearingRealResponse) => void
}): {
  state: Reactive<InteractState>
  phase: ComputedRef<InteractPhase>

  // Phase transitions
  startPaymentFlow: () => void
  startTrustlineFlow: () => void
  startClearingFlow: () => void
  selectNode: (nodeId: string) => void
  selectEdge: (edgeKey: string, anchor?: { x: number; y: number } | null) => void
  cancel: () => void

  // Actions
  confirmPayment: (amount: string) => Promise<void>
  confirmTrustlineCreate: (limit: string) => Promise<void>
  confirmTrustlineUpdate: (newLimit: string) => Promise<void>
  confirmTrustlineClose: () => Promise<void>
  confirmClearing: () => Promise<void>

  // Data
  participants: ComputedRef<ParticipantInfo[]>
  /** Backend-driven trustline list (cached), with snapshot fallback.
   * Used by Interact UI dropdowns and as a more authoritative source for capacity/limits.
   */
  trustlines: ComputedRef<TrustlineInfo[]>
  /** True while a trustlines fetch is in-flight (best-effort). */
  trustlinesLoading: ComputedRef<boolean>
  availableCapacity: ComputedRef<string | null>
  /** BUG-4: node IDs that should be highlighted as available targets in the current picking phase. */
  availableTargetIds: ComputedRef<Set<string>>

  // Flags
  busy: ComputedRef<boolean>
  canSendPayment: ComputedRef<boolean>
  canCreateTrustline: ComputedRef<boolean>

  /** REF-3: exported helper for canvas/UI wiring. */
  isPickingPhase: ComputedRef<boolean>

  // UI helpers (dropdowns)
  setPaymentFromPid: (pid: string | null) => void
  setPaymentToPid: (pid: string | null) => void
  setTrustlineFromPid: (pid: string | null) => void
  setTrustlineToPid: (pid: string | null) => void
  selectTrustline: (fromPid: string, toPid: string) => void

  // BUG-5: history log
  history: InteractHistoryEntry[]
} {
  // UX: keep the clearing preview visible long enough to be noticed/read.
  const CLEARING_PREVIEW_DWELL_MS = 800
  const CLEARING_RUNNING_DWELL_MS = 200

  const busyRef = ref(false)

  // Epoch that invalidates any in-flight async results.
  // Incremented:
  //  - on each `runBusy()` start (new operation)
  //  - on `cancel()` (user explicitly cancels/invalidates any result)
  //
  // IMPORTANT: `cancel()` must NOT clear `busy` while a promise is in-flight.
  // Therefore we track the operation that owns `busy` separately.
  let epoch = 0
  let busyOwnerEpoch: number | null = null

  const busy = computed(() => busyRef.value)

  // BUG-5: inline history log (last N actions)
  const { history, pushHistory } = useInteractHistory({ max: 20 })

  const dataCache = useInteractDataCache({
    actions: opts.actions,
    equivalent: opts.equivalent,
    snapshot: opts.snapshot,
    parseAmountStringOrNull,
  })

  const participants = dataCache.participants
  const trustlines = dataCache.trustlines
  const trustlinesLoading = dataCache.trustlinesLoading
  const refreshParticipants = dataCache.refreshParticipants
  const refreshTrustlines = dataCache.refreshTrustlines
  const invalidateTrustlinesCache = dataCache.invalidateTrustlinesCache
  const findActiveTrustline = dataCache.findActiveTrustline

  const fsm = useInteractFSM({
    snapshot: opts.snapshot,
    findActiveTrustline,
    onNodeClick: opts.onNodeClick,
  })

  const state = fsm.state
  const phase = fsm.phase
  const isPickingPhase = fsm.isPickingPhase

  const availableCapacity = computed(() => {
    // Prefer backend trustlines list when present (can be more authoritative than snapshot).
    // Payment `from -> to` uses capacity of trustline `to -> from` (creditor -> debtor).
    const tl = findActiveTrustline(state.toPid, state.fromPid)
    return parseAmountStringOrNull(tl?.available)
  })

  const canSendPayment = computed(() => {
    if (state.phase !== 'confirm-payment') return false
    if (!state.fromPid || !state.toPid || state.fromPid === state.toPid) return false
    const cap = parseAmountNumber(availableCapacity.value)
    return cap > 0
  })

  const canCreateTrustline = computed(() => {
    if (state.phase !== 'confirm-trustline-create') return false
    if (!state.fromPid || !state.toPid || state.fromPid === state.toPid) return false
    // Prefer fetched trustlines list when present; snapshot can be stale.
    const tl = findActiveTrustline(state.fromPid, state.toPid)
    if (tl) return false
    // Snapshot trustlines are included in `trustlines` computed already; if tl not found, assume none.
    return true
  })

  /** BUG-4: Node IDs available as picking targets in the current phase (for visual highlight). */
  const availableTargetIds = computed<Set<string>>(() => {
    const phase = state.phase

    // picking-payment-to: highlight nodes that have a trustline `to -> fromPid`
    if (phase === 'picking-payment-to' && state.fromPid) {
      const ids = new Set<string>()
      for (const tl of trustlines.value) {
        if (tl.to_pid === state.fromPid && isActiveStatus(tl.status)) {
          ids.add(tl.from_pid)
        }
      }
      // Fallback: if trustlines not loaded, show all participants except from
      if (ids.size === 0) {
        for (const p of participants.value) {
          if (p.pid !== state.fromPid) ids.add(p.pid)
        }
      }
      return ids
    }

    // picking-trustline-to: highlight all participants except fromPid
    if (phase === 'picking-trustline-to' && state.fromPid) {
      const ids = new Set<string>()
      for (const p of participants.value) {
        if (p.pid !== state.fromPid) ids.add(p.pid)
      }
      return ids
    }

    // picking-*-from: highlight all participants
    if (phase === 'picking-payment-from' || phase === 'picking-trustline-from') {
      const ids = new Set<string>()
      for (const p of participants.value) ids.add(p.pid)
      return ids
    }

    return new Set<string>()
  })

  function cancel() {
    // Invalidate any in-flight result (success/error) so it can't update state after cancel.
    epoch += 1
    fsm.resetToIdle()
  }

  function startPaymentFlow() {
    if (busyRef.value) return
    if (state.phase !== 'idle') return
    fsm.startPaymentFlow()
    void refreshParticipants()
  }

  function startTrustlineFlow() {
    if (busyRef.value) return
    if (state.phase !== 'idle') return
    fsm.startTrustlineFlow()
    void refreshParticipants()

    // Best-effort prefetch for trustline dropdowns / more up-to-date limits.
    void refreshTrustlines()
  }

  function startClearingFlow() {
    if (busyRef.value) return
    if (state.phase !== 'idle') return
    fsm.startClearingFlow()
  }

  function selectNode(nodeId: string) {
    if (busyRef.value) return
    fsm.selectNode(nodeId)
  }

  function selectEdge(edgeKey: string, anchor?: { x: number; y: number } | null) {
    if (busyRef.value) return
    fsm.selectEdge(edgeKey, anchor)

    // Opening edit UI: try to have trustlines list ready for dropdown + accurate details.
    void refreshParticipants()
    void refreshTrustlines()
  }

  async function runBusy<T>(
    fn: (ctx: { isCurrent: () => boolean; resetToIdle: () => void }) => Promise<T>,
  ): Promise<T | undefined> {
    if (busyRef.value) return undefined
    busyRef.value = true

    epoch += 1
    const myEpoch = epoch
    busyOwnerEpoch = myEpoch
    const isCurrent = () => epoch === myEpoch

    const resetToIdle = () => {
      // NOTE: do NOT bump epoch here; this is a success-path reset.
      fsm.resetToIdle()
    }

    try {
      return await fn({ isCurrent, resetToIdle })
    } catch (e: any) {
      // Don't leak errors into already-cancelled state.
      if (isCurrent()) state.error = String(e?.message ?? e)
      return undefined
    } finally {
      // Always clear `busy` when the owning promise settles, even if cancelled.
      if (busyOwnerEpoch === myEpoch) {
        busyRef.value = false
        busyOwnerEpoch = null
      }
    }
  }

  async function confirmPayment(amount: string): Promise<void> {
    fsm.clearError()
    const from = state.fromPid
    const to = state.toPid
    await runBusy(async ({ isCurrent, resetToIdle }) => {
      if (!from || !to) throw new Error('Select From and To first')
      await opts.actions.sendPayment(from, to, amount, opts.equivalent.value)
      if (!isCurrent()) return

      // BUG-5: log to history
      pushHistory('💸', `Payment ${amount} ${opts.equivalent.value}: ${from} → ${to}`)
      // Payment changes used/available; refresh trustlines so dropdowns/capacity can update.
      void refreshTrustlines({ force: true })
      resetToIdle()
    })
  }

  async function confirmTrustlineCreate(limit: string): Promise<void> {
    fsm.clearError()
    const from = state.fromPid
    const to = state.toPid
    await runBusy(async ({ isCurrent, resetToIdle }) => {
      if (!from || !to) throw new Error('Select From and To first')
      await opts.actions.createTrustline(from, to, limit, opts.equivalent.value)
      if (!isCurrent()) return

      // BUG-5: log to history
      pushHistory('🔗', `Trustline created: ${from} → ${to} (${limit})`)
      invalidateTrustlinesCache(opts.equivalent.value)
      void refreshTrustlines({ force: true })
      resetToIdle()
    })
  }

  async function confirmTrustlineUpdate(newLimit: string): Promise<void> {
    fsm.clearError()
    const from = state.fromPid
    const to = state.toPid
    await runBusy(async ({ isCurrent, resetToIdle }) => {
      if (!from || !to) throw new Error('Select trustline first')
      await opts.actions.updateTrustline(from, to, newLimit, opts.equivalent.value)
      if (!isCurrent()) return

      // BUG-5: log to history
      pushHistory('✏️', `Trustline updated: ${from} → ${to} → limit ${newLimit}`)
      const patchTrustlineLimitLocal = dataCache.patchTrustlineLimitLocal
      // Optimistic UI: patch cache immediately (fetch may be slow or fail silently).
      patchTrustlineLimitLocal(from, to, newLimit, opts.equivalent.value)
      invalidateTrustlinesCache(opts.equivalent.value)
      void refreshTrustlines({ force: true })
      resetToIdle()
    })
  }

  async function confirmTrustlineClose(): Promise<void> {
    fsm.clearError()
    const from = state.fromPid
    const to = state.toPid
    await runBusy(async ({ isCurrent, resetToIdle }) => {
      if (!from || !to) throw new Error('Select trustline first')
      await opts.actions.closeTrustline(from, to, opts.equivalent.value)
      if (!isCurrent()) return

      // BUG-5: log to history
      pushHistory('🗑️', `Trustline closed: ${from} → ${to}`)
      invalidateTrustlinesCache(opts.equivalent.value)
      void refreshTrustlines({ force: true })
      resetToIdle()
    })
  }

  // -------------------------
  // Dropdown-driven endpoint setters
  // -------------------------

  function setPaymentFromPid(pid: string | null) {
    if (busyRef.value) return
    fsm.setPaymentFromPid(pid)
  }

  function setPaymentToPid(pid: string | null) {
    if (busyRef.value) return
    fsm.setPaymentToPid(pid)
  }

  function setTrustlineFromPid(pid: string | null) {
    if (busyRef.value) return
    fsm.setTrustlineFromPid(pid)
  }

  function setTrustlineToPid(pid: string | null) {
    if (busyRef.value) return
    fsm.setTrustlineToPid(pid)
  }

  function selectTrustline(fromPid: string, toPid: string) {
    if (busyRef.value) return
    fsm.selectTrustline(fromPid, toPid)

    // NEW-1: entering edit flow via NodeCard should refresh cached data
    // similarly to edge click (selectEdge) so the panel can show backend-authoritative values.
    void refreshParticipants()
    void refreshTrustlines()
  }

  async function confirmClearing(): Promise<void> {
    fsm.clearError()
    await runBusy(async ({ isCurrent, resetToIdle }) => {
      // Two-phase: preview (store cycles) -> running (FX animation) -> idle.
      fsm.enterClearingPreview()

      const res = await opts.actions.runClearing(opts.equivalent.value)
      if (!isCurrent()) return
      fsm.setLastClearing(res)

      // BUG-5: log to history
      const clearedCycles = res.cleared_cycles ?? 0
      const clearedAmt = res.total_cleared_amount ?? '0'
      if (clearedCycles > 0) {
        pushHistory('🌀', `Clearing: ${clearedCycles} cycle(s), −${clearedAmt} ${opts.equivalent.value}`)
      } else {
        pushHistory('🌀', `Clearing: no cycles found`)
      }

      // BUG-3: trigger FX animation immediately after receiving clearing response.
      // This call is fire-and-forget — errors are intentionally ignored.
      if (res && typeof opts.onClearingDone === 'function') {
        try { opts.onClearingDone(res) } catch { /* ignore */ }
      }

      // Let Vue paint the preview at least once (even if very briefly).
      await Promise.resolve()
      if (!isCurrent()) return

      // Ensure preview has a readable dwell time.
      await new Promise((r) => setTimeout(r, CLEARING_PREVIEW_DWELL_MS))
      if (!isCurrent()) return

      fsm.enterClearingRunning()

      // Let Vue paint the running state at least once.
      await Promise.resolve()
      if (!isCurrent()) return

      // Minimal dwell for the running state (until proper SSE-driven wiring exists).
      await new Promise((r) => setTimeout(r, CLEARING_RUNNING_DWELL_MS))
      if (!isCurrent()) return

      resetToIdle()
    })
  }

  return {
    state,
    phase,

    startPaymentFlow,
    startTrustlineFlow,
    startClearingFlow,
    selectNode,
    selectEdge,
    cancel,

    confirmPayment,
    confirmTrustlineCreate,
    confirmTrustlineUpdate,
    confirmTrustlineClose,
    confirmClearing,

    participants,
    trustlines,
    trustlinesLoading,
    availableCapacity,
    availableTargetIds,

    busy,
    canSendPayment,
    canCreateTrustline,
    isPickingPhase,

    setPaymentFromPid,
    setPaymentToPid,
    setTrustlineFromPid,
    setTrustlineToPid,
    selectTrustline,

    history,
  }
}

