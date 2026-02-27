import { computed, ref, watch, type ComputedRef, type Reactive, type Ref } from 'vue'

import type { GraphSnapshot } from '../types'
import { parseAmountNumber, parseAmountStringOrNull } from '../utils/numberFormat'
import type { ParticipantInfo, SimulatorActionClearingRealResponse, TrustlineInfo } from '../api/simulatorTypes'
import { useInteractActions } from './useInteractActions'
import { useInteractDataCache } from './interact/useInteractDataCache'
import { useInteractFSM, type InteractPhase, type InteractState } from './interact/useInteractFSM'
import { useInteractHistory, type InteractHistoryEntry as InteractHistoryEntryT } from './interact/useInteractHistory'

export type { InteractPhase, InteractState }

export function useInteractMode(opts: {
  actions: ReturnType<typeof useInteractActions>
  /** Needed for payment-target cache keying (run-scoped endpoint). */
  runId: Ref<string>
  equivalent: Ref<string>
  snapshot: Ref<GraphSnapshot | null>
  onNodeClick?: (nodeId: string) => void
  /** BUG-3: called after successful clearing to trigger FX animation (gold pulse on cycle edges). */
  onClearingDone?: (result: SimulatorActionClearingRealResponse) => void
}): {
  state: Reactive<InteractState>
  phase: ComputedRef<InteractPhase>

  /** UI-level success toast message (outside FSM). */
  successMessage: Ref<string | null>

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
  /** Best-effort error signal for trustlines refresh failures (UI may show a degraded hint). */
  trustlinesLastError: ComputedRef<string | null>
  availableCapacity: ComputedRef<string | null>
  /** BUG-4: node IDs that should be highlighted as available targets in the current picking phase. */
  availableTargetIds: ComputedRef<Set<string> | undefined>

  /** Payment-specific targets for filtering the To dropdown (may be used outside picking phases, e.g. confirm). */
  paymentToTargetIds: ComputedRef<Set<string> | undefined>

  /** True while backend payment-targets query is in-flight for current (run, eq, fromPid, maxHops). */
  paymentTargetsLoading: ComputedRef<boolean>

  /** Current payment-targets max_hops policy used by the UI (drives multi-hop reachability). */
  paymentTargetsMaxHops: number

  /** Best-effort error signal for payment-targets refresh failures (UI may show a degraded hint). */
  paymentTargetsLastError: ComputedRef<string | null>

  // Flags
  busy: ComputedRef<boolean>
  /** True when user cancelled an operation, but the in-flight promise has not settled yet. */
  cancelling: ComputedRef<boolean>
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
  history: InteractHistoryEntryT[]
} {
  // UX: keep the clearing preview visible long enough to be noticed/read.
  const CLEARING_PREVIEW_DWELL_MS = 800
  const CLEARING_RUNNING_DWELL_MS = 200

  // NOTE: payment targets are backend-first (Phase 2.5) and include multi-hop reachability.
  // IMPORTANT: capacity shown in the UI is best-effort only (direct-hop hint).
  // Backend remains the source of truth for amount feasibility.
  //
  // Phase 2.5 requirement: support max_hops 6/8.
  // Policy:
  // - default: 6 (aligns with routing defaults / guardrails)
  // - optional: 8 (deeper search; may be more expensive)
  //
  // Gating:
  // - URL param override: ?payMaxHops=6|8 (useful for manual QA)
  // - Vite env override: VITE_PAYMENT_TARGETS_MAX_HOPS=6|8
  const PAYMENT_TARGETS_MAX_HOPS_DEFAULT = 6
  const PAYMENT_TARGETS_MAX_HOPS_DEEP = 8

  function readPaymentTargetsMaxHopsFromUrl(): number | null {
    try {
      const sp = new URLSearchParams(window.location.search)
      const raw = String(sp.get('payMaxHops') ?? '').trim()
      const n = Number(raw)
      if (n === PAYMENT_TARGETS_MAX_HOPS_DEFAULT) return n
      if (n === PAYMENT_TARGETS_MAX_HOPS_DEEP) return n
      return null
    } catch {
      return null
    }
  }

  function readPaymentTargetsMaxHopsFromEnv(): number | null {
    const raw = String((import.meta as any)?.env?.VITE_PAYMENT_TARGETS_MAX_HOPS ?? '').trim()
    const n = Number(raw)
    if (n === PAYMENT_TARGETS_MAX_HOPS_DEFAULT) return n
    if (n === PAYMENT_TARGETS_MAX_HOPS_DEEP) return n
    return null
  }

  const PAYMENT_TARGETS_MAX_HOPS =
    readPaymentTargetsMaxHopsFromUrl() ?? readPaymentTargetsMaxHopsFromEnv() ?? PAYMENT_TARGETS_MAX_HOPS_DEFAULT

  const busyRef = ref(false)

  // FB-1: UI-level success toast (kept outside FSM so it doesn't affect phase logic).
  const successMessage = ref<string | null>(null)

  const scheduleMicrotask: (fn: () => void) => void =
    typeof queueMicrotask === 'function' ? queueMicrotask : (fn) => Promise.resolve().then(fn)

  function setSuccessToastMessage(msg: string) {
    // Ensure repeated identical messages still retrigger watchers/timers.
    if (successMessage.value === msg) {
      successMessage.value = null
      scheduleMicrotask(() => {
        successMessage.value = msg
      })
      return
    }
    successMessage.value = msg
  }

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

  // P2.2: explicit UI signal when cancel was requested while an async action is in-flight.
  const cancellingRef = ref(false)
  const cancelling = computed(() => cancellingRef.value)

  // BUG-5: inline history log (last N actions)
  const { history, pushHistory } = useInteractHistory({ max: 20 })

  const dataCache = useInteractDataCache({
    actions: opts.actions,
    runId: opts.runId,
    equivalent: opts.equivalent,
    snapshot: opts.snapshot,
    parseAmountStringOrNull,
  })

  const participants = dataCache.participants
  const trustlines = dataCache.trustlines
  const trustlinesLoading = dataCache.trustlinesLoading
  const trustlinesLastError = dataCache.trustlinesLastError
  const refreshParticipants = dataCache.refreshParticipants
  const refreshTrustlines = dataCache.refreshTrustlines
  const refreshPaymentTargets = dataCache.refreshPaymentTargets
  const invalidateTrustlinesCache = dataCache.invalidateTrustlinesCache
  const findActiveTrustline = dataCache.findActiveTrustline

  const paymentTargetsLastError = dataCache.paymentTargetsLastError

  function normalizeEq(v: unknown): string {
    return String(v ?? '').trim().toUpperCase()
  }

  function normalizePid(v: unknown): string {
    return String(v ?? '').trim()
  }

  function normalizeRunId(v: unknown): string {
    return String(v ?? '').trim()
  }

  const fsm = useInteractFSM({
    snapshot: opts.snapshot,
    findActiveTrustline,
    onNodeClick: opts.onNodeClick,
  })

  const state = fsm.state
  const phase = fsm.phase
  const isPickingPhase = fsm.isPickingPhase

  const paymentTargetsActiveKey = computed(() => {
    const runId = normalizeRunId(opts.runId.value)
    const eq = normalizeEq(opts.equivalent.value)
    const fromPid = normalizePid(state.fromPid)
    if (!runId || !eq || !fromPid) return null
    return dataCache.paymentTargetsKey({ runId, eq, fromPid, maxHops: PAYMENT_TARGETS_MAX_HOPS })
  })

  const paymentTargetsLoading = computed(() => {
    const p = state.phase
    if (p !== 'picking-payment-to' && p !== 'confirm-payment') return false

    const key = paymentTargetsActiveKey.value
    if (!key) return false

    // In-flight request.
    if (dataCache.paymentTargetsLoadingByKey.value.get(key) === true) return true

    // Not fetched yet => still unknown.
    return !dataCache.paymentTargetsByKey.value.has(key)
  })

  function prefetchPaymentTargetsForCurrentFrom(o?: { force?: boolean }) {
    const p = state.phase
    if (p !== 'picking-payment-to' && p !== 'confirm-payment') return
    const runId = normalizeRunId(opts.runId.value)
    const fromPid = normalizePid(state.fromPid)
    if (!runId || !fromPid) return
    void refreshPaymentTargets({ fromPid, maxHops: PAYMENT_TARGETS_MAX_HOPS, ...(o?.force ? { force: true } : {}) })
  }

  // Refresh policy: when the underlying graph snapshot changes (tick / new graph),
  // revalidate payment targets for the current From (if the payment flow is active).
  watch(
    () => String(opts.snapshot.value?.generated_at ?? ''),
    () => {
      prefetchPaymentTargetsForCurrentFrom({ force: true })
    },
    { immediate: false },
  )

  const availableCapacity = computed(() => {
    // Prefer backend trustlines list when present (can be more authoritative than snapshot).
    // Payment `from -> to` uses capacity of trustline `to -> from` (creditor -> debtor).
    const tl = findActiveTrustline(state.toPid, state.fromPid)
    return parseAmountStringOrNull(tl?.available)
  })

  const canSendPayment = computed(() => {
    if (state.phase !== 'confirm-payment') return false
    if (!state.fromPid || !state.toPid || state.fromPid === state.toPid) return false

    // In multi-hop mode, direct trustline capacity is NOT a hard gate.
    // Gate only by backend-first reachability targets when known.
    const targets = paymentToTargetIds.value
    // Tri-state gating (Phase 2.5): allow confirm when reachability is unknown/degraded.
    // NOTE: refreshPaymentTargets() stores an empty Set on error for deterministic UI;
    // therefore we must also treat `paymentTargetsLastError` as degraded/unknown here.
    if (targets === undefined) return true
    if (paymentTargetsLastError.value) return true
    return targets.has(state.toPid)
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
  const availableTargetIds = computed<Set<string> | undefined>(() => {
    const phase = state.phase

    // picking-payment-to: highlight the same targets as the To dropdown.
    if (phase === 'picking-payment-to') {
      // Keep canvas highlight consistent with dropdown tri-state wiring.
      // - while trustlines are loading => unknown (dropdown shows degraded fallback)
      // - when payment-targets refresh failed => unknown (dropdown shows degraded fallback)
      // - otherwise: use backend-first targets (Set, incl. empty)
      if (trustlinesLoading.value) return undefined
      if (paymentTargetsLastError.value) return undefined
      // Tri-state contract: `undefined` strictly means unknown/loading.
      // Known-empty is represented as an empty Set (no fallback).
      return paymentToTargetIds.value
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

    // Outside picking phases: no meaningful targets for highlight.
    // Keep semantics strict: `undefined` is reserved for unknown/loading only.
    return new Set<string>()
  })

  /**
   * Payment targets for dropdown filtering.
   *
   * Contract:
   *  - `undefined` => unknown (backend request in-flight OR not yet fetched)
   *  - `Set` (incl. empty) => known
   *
   * This keeps dropdown tri-state wiring deterministic and separate from
   * `availableTargetIds` semantics used for canvas highlighting.
   */
  const paymentToTargetIds = computed<Set<string> | undefined>(() => {
    const p = state.phase
    if (p !== 'picking-payment-to' && p !== 'confirm-payment') return new Set()

    const runId = normalizeRunId(opts.runId.value)
    const eq = normalizeEq(opts.equivalent.value)
    const fromPid = normalizePid(state.fromPid)
    if (!runId || !eq || !fromPid) return new Set()

    const key = dataCache.paymentTargetsKey({ runId, eq, fromPid, maxHops: PAYMENT_TARGETS_MAX_HOPS })

    // While in-flight OR not yet fetched, keep tri-state as unknown.
    if (dataCache.paymentTargetsLoadingByKey.value.get(key) === true) return undefined

    const cached = dataCache.paymentTargetsByKey.value.get(key)
    if (cached) return cached

    // Not fetched yet.
    return undefined
  })

  function cancel() {
    // Invalidate any in-flight result (success/error) so it can't update state after cancel.
    epoch += 1
    fsm.resetToIdle()

    // If an operation is still in-flight, expose a user-facing hint that cancellation is pending.
    // This does NOT clear busy; busy is cleared only when the owning promise settles.
    if (busyRef.value) {
      cancellingRef.value = true
    }
  }

  function startPaymentFlow() {
    if (busyRef.value) return
    if (state.phase !== 'idle') return
    fsm.startPaymentFlow()
    void refreshParticipants()

    // MP-6a: best-effort prefetch to make `availableTargetIds` tri-state reliable.
    void refreshTrustlines({ force: true })

    // Phase 2.5: payment-targets prefetch (runs only once From is chosen).
    prefetchPaymentTargetsForCurrentFrom()
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

    // If selecting From moved us into picking-payment-to, start payment-targets fetch.
    prefetchPaymentTargetsForCurrentFrom()
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

    // New operation: clear any stale cancelling flag.
    cancellingRef.value = false

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
      if (isCurrent()) {
        const msg = String(e?.message ?? e)
        // Ensure repeated identical errors still retrigger the ErrorToast timer.
        if (state.error === msg) {
          state.error = null
          scheduleMicrotask(() => {
            if (isCurrent()) state.error = msg
          })
        } else {
          state.error = msg
        }
      }
      return undefined
    } finally {
      // Always clear `busy` when the owning promise settles, even if cancelled.
      if (busyOwnerEpoch === myEpoch) {
        busyRef.value = false
        busyOwnerEpoch = null
        cancellingRef.value = false
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

      setSuccessToastMessage(`Payment sent: ${amount} ${opts.equivalent.value}`)

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

      setSuccessToastMessage(`Trustline created: ${from} → ${to}`)

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

      setSuccessToastMessage(`Limit updated: ${newLimit} ${opts.equivalent.value}`)

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

      setSuccessToastMessage(`Trustline closed: ${from} → ${to}`)

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

    // From change affects To target set.
    prefetchPaymentTargetsForCurrentFrom()
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

      const settled = res?.cleared_cycles ?? 0
      const total = Array.isArray(res?.cycles) ? res.cycles.length : settled
      successMessage.value = `Clearing done: ${settled}/${total} cycles`

      resetToIdle()
    })
  }

  return {
    state,
    phase,

    successMessage,

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
    trustlinesLastError,
    availableCapacity,
    availableTargetIds,
    paymentToTargetIds,
    paymentTargetsLoading,
    paymentTargetsLastError,

    paymentTargetsMaxHops: PAYMENT_TARGETS_MAX_HOPS,

    busy,
    cancelling,
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



