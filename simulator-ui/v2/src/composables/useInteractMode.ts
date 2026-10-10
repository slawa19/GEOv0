import { computed, ref, watch, type ComputedRef, type Reactive, type Ref } from 'vue'

import type { GraphSnapshot } from '../types'
import { extractErrorMessage, withRequestRef } from '../utils/errorMessage'
import { interactText } from '../i18n/interactStrings'
import { clearingRefusalText, paymentRefusalText } from '../utils/paymentRefusalText'
import { parseAmountNumber, parseAmountStringOrNull } from '../utils/numberFormat'
import type { ParticipantInfo, SimulatorActionClearingRealResponse, TrustlineInfo } from '../api/simulatorTypes'
import { useInteractActions } from './useInteractActions'
import { useInteractDataCache } from './interact/useInteractDataCache'
import type { TrustlinesFetchState } from './interact/trustlinesSourceState'
import { useInteractFSM, type InteractPhase, type InteractState } from './interact/useInteractFSM'
import { useInteractHistory, type InteractHistoryEntry as InteractHistoryEntryT } from './interact/useInteractHistory'
import { createPaymentIntentKeeper, isKeySpent, isValidIdempotencyKey, type PaymentIntent } from './interact/paymentIntent'

export type { InteractPhase, InteractState }

/** One step of a route of a committed manual payment, with the names to show. */
export type ManualPaymentHop = { from: string; to: string; fromName: string; toName: string; amount: string }

/**
 * What the manual-payment panel shows after an attempt (037 A2). `success`: the payment is COMMITTED and stays on screen
 * until dismissed. `unknown`: an attempt of the payment has no verdict - the user checks / repeats under the same key.
 */
export type ManualPaymentOutcome =
  | {
      kind: 'success'
      paymentId: string
      status: string
      amount: string
      equivalent: string
      fromPid: string
      toPid: string
      fromName: string
      toName: string
      routes: Array<{ hops: ManualPaymentHop[] }>
    }
  | {
      kind: 'unknown'
      message: string
      amount: string
      equivalent: string
      fromPid: string
      toPid: string
      fromName: string
      toName: string
      /** The unresolved payment belongs to another run than the one open now: it cannot be checked from here. */
      runMismatch: boolean
    }

/** The server's estimate for the chosen recipient (`payment-targets` with `include_max_available`), with the state of the answer. */
export type PaymentTargetEstimate =
  | { state: 'loading' }
  | { state: 'failed' }
  | { state: 'received'; hops: number; maxAvailable: string | null }

export function useInteractMode(opts: {
  actions: ReturnType<typeof useInteractActions>
  /** Needed for payment-target cache keying (run-scoped endpoint). */
  runId: Ref<string>
  equivalent: Ref<string>
  snapshot: Ref<GraphSnapshot | null>
  onNodeClick?: (nodeId: string) => void
  /**
   * Where the one unresolved manual payment is kept across a page reload (`sessionStorage` by default). `null` means there
   * is no storage, and then NO manual payment is sent - the same as when the browser refuses the storage: a payment whose
   * record cannot be saved could not be checked safely after a reload.
   */
  intentStorage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null
  /** BUG-3: called after successful clearing to trigger FX animation (gold pulse on cycle edges). */
  onClearingDone?: (result: SimulatorActionClearingRealResponse) => void
}): {
  state: Reactive<InteractState>
  phase: ComputedRef<InteractPhase>

  /** UI-level success toast message (outside FSM). */
  successMessage: Ref<string | null>

  // Phase transitions
  startPaymentFlow: () => void
  /** Atomically start payment flow with pre-filled FROM (skips picking-payment-from phase). */
  startPaymentFlowWithFrom: (fromPid: string) => void
  startTrustlineFlow: () => void
  /** Atomically start trustline flow with pre-filled FROM (skips picking-trustline-from phase). */
  startTrustlineFlowWithFrom: (fromPid: string) => void
  startClearingFlow: () => void
  selectNode: (nodeId: string) => void
  selectEdge: (edgeKey: string, anchor?: { x: number; y: number } | null) => boolean
  cancel: () => void
  /**
   * 037 C: the user clicked EMPTY canvas. Cancels what is only being filled in, but NOT a held result (a finished clearing or its
   * refusal, a committed payment): those end by Close/Esc (`cancel`) or by a deliberate replacement, never by a stray click.
   */
  cancelFromCanvas: () => void

  // Actions
  confirmPayment: (amount: string) => Promise<void>
  /** Repeat the payment whose result is unknown: the same frozen intent under the same idempotency key. */
  retryPayment: () => Promise<void>
  /** Leave the result screen of a committed payment for the recipient step (same sender). */
  dismissPaymentResult: () => void
  /**
   * A person's explicit decision to give up the unresolved payment (its first attempt may have been made): the held key and
   * frozen intent are dropped and another payment may be confirmed. Nothing else calls it - not Esc, not a closed panel.
   */
  discardUnresolvedPayment: () => void
  /** The committed payment / the unknown result the panel shows (null: nothing to show). */
  paymentOutcome: ComputedRef<ManualPaymentOutcome | null>
  /** The server's estimate for the chosen recipient, or null when no recipient / sender is chosen. */
  paymentTargetEstimate: ComputedRef<PaymentTargetEstimate | null>
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
  /** `F-013-7`: source state as a state (never asked / loading / failed / answered). */
  trustlinesFetchState: ComputedRef<TrustlinesFetchState>
  /** `F-013-7`: the pair's row as the source actually answered it (no snapshot fallback). */
  findAnsweredTrustline: (from: string | null, to: string | null) => TrustlineInfo | null
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
    isCanvasNodePickPhase: ComputedRef<boolean>
  // UI helpers (dropdowns)
  setPaymentFromPid: (pid: string | null) => void
  setPaymentToPid: (pid: string | null) => void
  setTrustlineFromPid: (pid: string | null) => void
  setTrustlineToPid: (pid: string | null) => void
  selectTrustline: (fromPid: string, toPid: string) => void

  // BUG-5: history log
  history: InteractHistoryEntryT[]
} {
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
    const raw = String(import.meta.env?.VITE_PAYMENT_TARGETS_MAX_HOPS ?? '').trim()
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

  // RACE-4: each busy operation has its own AbortController, tied to epoch.
  // `cancel()` aborts the active controller (best-effort) so the underlying HTTP is actually interrupted.
  let activeAbort: { epoch: number; ctrl: AbortController } | null = null

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
  const trustlinesFetchState = dataCache.trustlinesFetchState
  const findAnsweredTrustline = dataCache.findAnsweredTrustline
  const refreshParticipants = dataCache.refreshParticipants
  const refreshTrustlines = dataCache.refreshTrustlines
  const refreshPaymentTargets = dataCache.refreshPaymentTargets
  const invalidateTrustlinesCache = dataCache.invalidateTrustlinesCache
  const findActiveTrustline = dataCache.findActiveTrustline

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
    const isCanvasNodePickPhase = fsm.isCanvasNodePickPhase
  // 037 A2: the manual payment's result on screen, and the life of its idempotency key (see `paymentIntent.ts`).
  // `paymentOutcomeRef` holds only the SUCCESS on screen. An unresolved payment is not in it: it is read from the keeper (below),
  // so it survives a closed panel, `cancel()`, and - through the keeper's storage - a re-created composable or a reload.
  const paymentOutcomeRef = ref<ManualPaymentOutcome | null>(null)
  const paymentIntents = createPaymentIntentKeeper({
    runId: () => normalizeRunId(opts.runId.value),
    ...(opts.intentStorage !== undefined ? { storage: opts.intentStorage } : {}),
  })
  const intentVersion = ref(0)
  const touchIntents = () => {
    intentVersion.value += 1
  }
  const paymentOutcome = computed<ManualPaymentOutcome | null>(() => {
    void intentVersion.value
    const record = paymentIntents.peek()
    if (record && record.unknown) {
      const { intent } = record
      return {
        kind: 'unknown',
        message: interactText('unknownNoAnswer', {
          amount: intent.amount, unit: intent.equivalent, from: participantName(intent.from), to: participantName(intent.to),
        }),
        amount: intent.amount,
        equivalent: intent.equivalent,
        fromPid: intent.from,
        toPid: intent.to,
        fromName: participantName(intent.from),
        toName: participantName(intent.to),
        runMismatch: intent.runId !== normalizeRunId(opts.runId.value),
      }
    }
    return paymentOutcomeRef.value
  })
  watch(
    () => [state.fromPid, state.toPid, state.phase],
    () => {
      // Another pair, or no payment step any more: what was on screen belonged to the earlier one.
      paymentOutcomeRef.value = null
    },
  )

  function participantName(pid: string): string {
    const found = participants.value.find((p) => p.pid === pid)
    const name = String(found?.name ?? '').trim()
    return name || pid
  }

  const paymentTargetsActiveKey = computed(() => {
    const runId = normalizeRunId(opts.runId.value)
    const eq = normalizeEq(opts.equivalent.value)
    const fromPid = normalizePid(state.fromPid)
    if (!runId || !eq || !fromPid) return null
    return dataCache.paymentTargetsKey({ runId, eq, fromPid, maxHops: PAYMENT_TARGETS_MAX_HOPS })
  })

  const paymentTargetsLastError = computed(() => {
    const key = paymentTargetsActiveKey.value
    return key ? (dataCache.paymentTargetsLastErrorByKey.value.get(key) ?? null) : null
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
  //
  // ВТОРОЙ ПОТРЕБИТЕЛЬ ТОЙ ЖЕ РЕВИЗИИ, и он обязан двигаться вместе с первым (`F-013-2` /
  // `T1303`, 2026-09-10). Очистка кэша в `useInteractDataCache` делает ответ ПУСТЫМ, а не
  // свежим; перезапрашивает — вот этот watch. Если добавить `data_revision` только в один из
  // двух, после патча останется либо устаревший набор целей, либо пустой, и оба варианта хуже
  // третьего. Это тот же урок «сигнал построен в двух местах», что и в `F-013-4`.
  watch(
    () => `${String(opts.snapshot.value?.generated_at ?? '')}|${opts.snapshot.value?.data_revision ?? 0}`,
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

  const paymentTargetEstimate = computed<PaymentTargetEstimate | null>(() => {
    const key = paymentTargetsActiveKey.value
    const to = normalizePid(state.toPid)
    if (!key || !to) return null
    if (dataCache.paymentTargetsLoadingByKey.value.get(key) === true) return { state: 'loading' }
    if (dataCache.paymentTargetsLastErrorByKey.value.get(key)) return { state: 'failed' }
    const details = dataCache.paymentTargetDetailsByKey.value.get(key)
    if (!details) return { state: 'loading' }
    const d = details.get(to)
    // A recipient the answer does not list has no estimate to show (the To list is filtered by the same answer).
    if (!d) return null
    return { state: 'received', hops: d.hops, maxAvailable: d.max_available }
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

  /** A result the user has not closed yet: it must survive a click that was not aimed at it. */
  const resultHeld = computed(() =>
    (state.phase === 'clearing-preview' && (!!state.lastClearing || !!state.clearingFailure))
    || paymentOutcome.value?.kind === 'success',
  )

  function cancelFromCanvas() {
    if (resultHeld.value) return
    cancel()
  }

  function cancel() {
    // Invalidate any in-flight result (success/error) so it can't update state after cancel.
    // IMPORTANT: bump epoch BEFORE abort so an AbortError can't leak into state.error.
    epoch += 1
    paymentOutcomeRef.value = null

    // RACE-4: abort active HTTP (best-effort).
    const ctrl = activeAbort?.ctrl
    activeAbort = null
    try {
      ctrl?.abort()
    } catch {
      // ignore
    }
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

  function startPaymentFlowWithFrom(fromPid: string) {
    if (busyRef.value) return
    if (state.phase !== 'idle') return
    fsm.startPaymentFlowWithFrom(fromPid)
    void refreshParticipants()

    // MP-6a: best-effort prefetch to make `availableTargetIds` tri-state reliable.
    void refreshTrustlines({ force: true })

    // Phase 2.5: payment-targets prefetch — From is already known.
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

  function startTrustlineFlowWithFrom(fromPid: string) {
    if (busyRef.value) return
    if (state.phase !== 'idle') return
    fsm.startTrustlineFlowWithFrom(fromPid)
    void refreshParticipants()

    // Best-effort prefetch for trustline dropdowns / more up-to-date limits.
    void refreshTrustlines()
  }

  function startClearingFlow() {
    if (busyRef.value) return
    if (state.phase !== 'idle') return
    fsm.startClearingFlow()
    successMessage.value = null // an earlier clearing's announcement is not this one's
    // 037 C: the result names participants. Clearing may be the first thing done in a session, so the list is asked for now,
    // while the user reads the confirm step (the graph snapshot's nodes are the fallback; an id is shown only when neither knows).
    void refreshParticipants()
  }

  function selectNode(nodeId: string) {
    if (busyRef.value) return
    fsm.selectNode(nodeId)

    // If selecting From moved us into picking-payment-to, start payment-targets fetch.
    prefetchPaymentTargetsForCurrentFrom()
  }

  function selectEdge(edgeKey: string, anchor?: { x: number; y: number } | null) {
    if (busyRef.value) return false
    // A line clicked while a result is held does not replace it: the user closes the result first (037 C, decided).
    if (resultHeld.value) return false
    fsm.selectEdge(edgeKey, anchor)

    // Opening edit UI: try to have trustlines list ready for dropdown + accurate details.
    void refreshParticipants()
    void refreshTrustlines()
    return true
  }

  async function runBusy<T>(
    fn: (ctx: { isCurrent: () => boolean; resetToIdle: () => void; signal: AbortSignal }) => Promise<T>,
  ): Promise<T | undefined> {
    if (busyRef.value) return undefined
    busyRef.value = true

    // New operation: clear any stale cancelling flag.
    cancellingRef.value = false

    epoch += 1
    const myEpoch = epoch
    busyOwnerEpoch = myEpoch
    const ctrl = new AbortController()
    activeAbort = { epoch: myEpoch, ctrl }
    const isCurrent = () => epoch === myEpoch

    const resetToIdle = () => {
      // NOTE: do NOT bump epoch here; this is a success-path reset.
      fsm.resetToIdle()
    }

    try {
      return await fn({ isCurrent, resetToIdle, signal: ctrl.signal })
    } catch (error) {
      // Don't leak errors into already-cancelled state.
      if (isCurrent()) {
        const msg = extractErrorMessage(error)
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
      // Clear abort controller only if it still belongs to this operation.
      if (activeAbort?.epoch === myEpoch) activeAbort = null

      // Always clear `busy` when the owning promise settles, even if cancelled.
      if (busyOwnerEpoch === myEpoch) {
        busyRef.value = false
        busyOwnerEpoch = null
        cancellingRef.value = false
      }
    }
  }

  async function confirmPayment(amount: string): Promise<void> {
    // A second Confirm while the first is in flight is ignored BEFORE it can touch the held intent (R5).
    if (busyRef.value) return
    fsm.clearError()
    // 028 F-028-47 (B1): the equivalent of the action, not the one selected when it returns.
    const eq = opts.equivalent.value
    const from = state.fromPid
    const to = state.toPid
    if (!from || !to) {
      await runBusy(async () => {
        throw new Error('Select From and To first')
      })
      return
    }
    await submitPayment({ runId: normalizeRunId(opts.runId.value), from, to, equivalent: eq, amount })
  }

  /** The result of an attempt that has no verdict: the payment is repeated by this, under the same key - in ITS run. */
  async function retryPayment(): Promise<void> {
    if (busyRef.value) return
    const record = paymentIntents.peek()
    if (!record || !record.unknown) return
    // R4: the frozen intent belongs to one run. Another run is not asked about it (the action would go to the current run).
    if (record.intent.runId !== normalizeRunId(opts.runId.value)) return
    fsm.clearError()
    await submitPayment(record.intent)
  }

  function discardUnresolvedPayment(): void {
    if (busyRef.value) return
    paymentIntents.discard()
    touchIntents()
  }

  function dismissPaymentResult(): void {
    if (busyRef.value) return
    paymentOutcomeRef.value = null
    fsm.setPaymentToPid(null)
  }

  /**
   * One attempt of a manual payment. The key belongs to the INTENT (`paymentIntent.ts`): the same frozen intent is sent
   * under the same key until the server says the key is spent or the payment is known to be made. A change of the intent is
   * a new payment with a new key - except while an intent is UNRESOLVED: then nothing else is sent at all (`blocked`).
   */
  async function submitPayment(intent: PaymentIntent): Promise<void> {
    if (busyRef.value) return
    const { from, to, equivalent: eq, amount } = intent
    const begun = paymentIntents.begin(intent)
    if (begun.kind === 'unreadable') {
      state.error = interactText('storageUnreadable')
      touchIntents()
      return
    }
    if (begun.kind === 'blocked') {
      state.error = interactText('unknownBlocksNew')
      touchIntents()
      return
    }
    const record = begun.record
    // Defence in depth: a payment - and above all the repeat of an unresolved one - never leaves without a valid key. A
    // missing key would not be a check, it would be a NEW payment.
    if (!isValidIdempotencyKey(record.key)) {
      state.error = interactText('noValidKey')
      touchIntents()
      return
    }
    const names = { from: participantName(from), to: participantName(to) }
    successMessage.value = null
    await runBusy(async ({ isCurrent, signal }) => {
      let res: Awaited<ReturnType<typeof opts.actions.sendPayment>>
      // The record that lets a reload check this payment is saved BEFORE the request leaves; if it cannot be, nothing is sent.
      if (!paymentIntents.markSent(record)) throw new Error(interactText('storageRefused'))
      try {
        res = await opts.actions.sendPayment(from, to, amount, eq, { signal, idempotencyKey: record.key })
      } catch (e: unknown) {
        const err = (e && typeof e === 'object' ? e : {}) as {
          outcomeUnknown?: unknown
          details?: unknown
          code?: unknown
          message?: unknown
        }
        const spent = isKeySpent(err.details)
        const unknownNow = err.outcomeUnknown === true
        // Recorded whether or not the panel is still open: a request closed in flight is an unresolved payment all the same (R2).
        if (spent) paymentIntents.settle(record)
        else if (unknownNow) paymentIntents.markUnknown(record)
        else paymentIntents.markRefused(record)
        touchIntents()
        // RUN_TERMINAL, 401, 403 and a retryable 409 after an unknown attempt say nothing about THAT payment: the record
        // stays unresolved, and the banner is read from it.
        // 028 F-028-51: the refusal text is composed here from code + reason + details (owner В-6).
        const text = unknownNow
          ? err.code === 'TIMEOUT' && typeof err.message === 'string'
            ? err.message
            : interactText('unknownNoAnswer', { amount, unit: eq, from: names.from, to: names.to })
          : paymentRefusalText(e, eq, undefined, { keyed: true })
        // The correlation id of the request goes with the text, like every user-facing error (AGENTS section 12).
        throw new Error(withRequestRef(text, e))
      }

      // The answer is a verdict only if it says COMMITTED: a success-shaped answer for a stored refusal is not a payment.
      const status = String(res.status ?? '').toUpperCase()
      if (status !== 'COMMITTED') {
        const refused = status === 'ABORTED'
        if (refused) paymentIntents.settle(record)
        else paymentIntents.markUnknown(record)
        touchIntents()
        throw new Error(interactText('notCommitted', { status: String(res.status) }))
      }

      paymentIntents.settle(record)
      touchIntents()
      if (!isCurrent()) return

      paymentOutcomeRef.value = {
        kind: 'success',
        paymentId: String(res.payment_id),
        status: String(res.status),
        amount: String(res.amount ?? amount),
        equivalent: String(res.equivalent ?? eq),
        fromPid: from,
        toPid: to,
        fromName: names.from,
        toName: names.to,
        // A route without a step has nothing to show; it is not drawn as an empty line.
        routes: (res.routes ?? []).filter((route) => route.hops.length > 0).map((route) => ({
          hops: route.hops.map((hop) => ({
            from: hop.from,
            to: hop.to,
            fromName: participantName(hop.from),
            toName: participantName(hop.to),
            amount: hop.amount,
          })),
        })),
      }
      setSuccessToastMessage(`Payment sent: ${amount} ${eq}`)

      // BUG-5: log to history (037 A2: names, not pids)
      pushHistory('💸', `Payment ${amount} ${eq}: ${names.from} → ${names.to}`)
      // Payment changes used/available; refresh trustlines so dropdowns/capacity can update.
      void refreshTrustlines({ force: true })
      // The panel stays on the result: it is dismissed by the user (`dismissPaymentResult` / `cancel`), not by a reset.
    })
  }

  async function confirmTrustlineCreate(limit: string): Promise<void> {
    fsm.clearError()
    // 028 F-028-47 (B1): the equivalent of the action, not the one selected when it returns.
    const eq = opts.equivalent.value
    const from = state.fromPid
    const to = state.toPid
    await runBusy(async ({ isCurrent, resetToIdle, signal }) => {
      if (!from || !to) throw new Error('Select From and To first')
      await opts.actions.createTrustline(from, to, limit, eq, { signal })
      if (!isCurrent()) return

      setSuccessToastMessage(`Trustline created: ${from} → ${to}`)

      // BUG-5: log to history
      pushHistory('🔗', `Trustline created: ${from} → ${to} (${limit})`)
      invalidateTrustlinesCache(eq)
      void refreshTrustlines({ force: true })
      resetToIdle()
    })
  }

  async function confirmTrustlineUpdate(newLimit: string): Promise<void> {
    fsm.clearError()
    // 028 F-028-47 (B1): the equivalent of the action, not the one selected when it returns.
    const eq = opts.equivalent.value
    const from = state.fromPid
    const to = state.toPid
    await runBusy(async ({ isCurrent, resetToIdle, signal }) => {
      if (!from || !to) throw new Error('Select trustline first')
      await opts.actions.updateTrustline(from, to, newLimit, eq, { signal })
      if (!isCurrent()) return

      setSuccessToastMessage(`Limit updated: ${newLimit} ${eq}`)

      // BUG-5: log to history
      pushHistory('✏️', `Trustline updated: ${from} → ${to} → limit ${newLimit}`)
      const patchTrustlineLimitLocal = dataCache.patchTrustlineLimitLocal
      // Optimistic UI: patch cache immediately (fetch may be slow or fail silently).
      patchTrustlineLimitLocal(from, to, newLimit, eq)
      invalidateTrustlinesCache(eq)
      void refreshTrustlines({ force: true })
      resetToIdle()
    })
  }

  async function confirmTrustlineClose(): Promise<void> {
    fsm.clearError()
    // 028 F-028-47 (B1): the equivalent of the action, not the one selected when it returns.
    const eq = opts.equivalent.value
    const from = state.fromPid
    const to = state.toPid
    await runBusy(async ({ isCurrent, resetToIdle, signal }) => {
      if (!from || !to) throw new Error('Select trustline first')
      const res = await opts.actions.closeTrustline(from, to, eq, { signal })
      if (!isCurrent()) return

      // 026: "closed" only when the backend says so; otherwise the close is a request (limit 0 until repaid).
      const msg = res.status === 'closed'
        ? `Trustline closed: ${from} → ${to}`
        : `Close requested: ${from} → ${to} (closes when the debt is repaid)`
      setSuccessToastMessage(msg)

      // BUG-5: log to history
      pushHistory('🗑️', msg)
      if (res.status !== 'closed') dataCache.patchTrustlineLimitLocal(from, to, '0', eq)
      invalidateTrustlinesCache(eq)
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
    // 028 F-028-47 (B1): the equivalent of the action, not the one selected when it returns.
    const eq = opts.equivalent.value
    await runBusy(async ({ isCurrent, signal }) => {
      // 037 C: the phase after the confirm step is "the answer is awaited, then the result is shown" (its name, `clearing-preview`,
      // is the old one). It is left only by the user (Close/Esc -> `cancel()`) or by the next clearing: no timer ends it.
      fsm.enterClearingPreview()

      // 031 item 17: the step refusal (409 CLEARING_REFUSED, 030 S2) is shown as the client's text, not the server hint.
      const res = await opts.actions.runClearing(eq, { signal }).catch((e: unknown) => {
        const message = clearingRefusalText(e, eq)
        // The text stays as a state of the panel when the error toast is gone (the toast clears `state.error`).
        if (isCurrent()) fsm.setClearingFailure(message)
        throw new Error(message)
      })
      if (!isCurrent()) return
      fsm.setLastClearing(res)

      // BUG-5: log to history
      const clearedCycles = res.cleared_cycles
      const clearedAmt = res.total_cleared_amount ?? '0'
      if (clearedCycles > 0) {
        pushHistory('🌀', `Clearing: ${clearedCycles} cycle(s), −${clearedAmt} ${eq}`)
      } else {
        pushHistory('🌀', `Clearing: no cycles found`)
      }

      // BUG-3: trigger FX animation immediately after receiving clearing response.
      // This call is fire-and-forget — errors are intentionally ignored.
      if (res && typeof opts.onClearingDone === 'function' && opts.equivalent.value === eq) {
        try { opts.onClearingDone(res) } catch { /* ignore */ }
      }

      // A success toast only when something was cleared: "no cycles" is a state of the panel, not a success.
      if (res.cleared_cycles > 0) {
        successMessage.value = `Clearing done: ${res.cleared_cycles}/${res.cycles.length} cycles`
      }
    })
  }

  return {
    state,
    phase,

    successMessage,

    startPaymentFlow,
    startPaymentFlowWithFrom,
    startTrustlineFlow,
    startTrustlineFlowWithFrom,
    startClearingFlow,
    selectNode,
    selectEdge,
    cancel,
    cancelFromCanvas,

    confirmPayment,
    retryPayment,
    dismissPaymentResult,
    discardUnresolvedPayment,
    paymentOutcome,
    paymentTargetEstimate,
    confirmTrustlineCreate,
    confirmTrustlineUpdate,
    confirmTrustlineClose,
    confirmClearing,

    participants,
    trustlines,
    trustlinesLoading,
    trustlinesLastError,
    trustlinesFetchState,
    findAnsweredTrustline,
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
    isCanvasNodePickPhase,

    setPaymentFromPid,
    setPaymentToPid,
    setTrustlineFromPid,
    setTrustlineToPid,
    selectTrustline,

    history,
  }
}


