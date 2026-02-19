import { computed, reactive, ref, watch, type ComputedRef, type Reactive, type Ref } from 'vue'

/** BUG-5: Entry in the Interact Mode history log. */
export type InteractHistoryEntry = {
  id: number
  icon: string
  text: string
  timeText: string
  timestampMs: number
}

import type { GraphSnapshot, GraphLink } from '../types'
import { keyEdge, parseEdgeKey } from '../utils/edgeKey'
import { parseAmountNumber } from '../utils/amount'
import { isActiveStatus } from '../utils/status'
import type { ParticipantInfo, SimulatorActionClearingRealResponse, TrustlineInfo } from '../api/simulatorTypes'
import { useInteractActions } from './useInteractActions'

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
  error: string | null

  /** Last clearing action response (populated in `clearing-preview`). */
  lastClearing: SimulatorActionClearingRealResponse | null
}

function findActiveLink(snapshot: GraphSnapshot | null, from: string | null, to: string | null): GraphLink | null {
  if (!snapshot || !from || !to) return null
  for (const l of snapshot.links ?? []) {
    if (l.source === from && l.target === to && isActiveStatus(l.status)) return l
  }
  return null
}

function parseAmountStringOrNull(v: unknown): string | null {
  if (v == null) return null
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : null
  if (typeof v === 'string') return v
  return null
}

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
  selectEdge: (edgeKey: string) => void
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
  availableCapacity: ComputedRef<string | null>
  /** BUG-4: node IDs that should be highlighted as available targets in the current picking phase. */
  availableTargetIds: ComputedRef<Set<string>>

  // Flags
  busy: ComputedRef<boolean>
  canSendPayment: ComputedRef<boolean>
  canCreateTrustline: ComputedRef<boolean>

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

  const state = reactive<InteractState>({
    phase: 'idle',
    fromPid: null,
    toPid: null,
    selectedEdgeKey: null,
    error: null,

    lastClearing: null,
  })

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

  const phase = computed(() => state.phase)
  const busy = computed(() => busyRef.value)

  // BUG-5: inline history log (last N actions)
  const MAX_HISTORY = 20
  const history = reactive<InteractHistoryEntry[]>([])
  let nextHistoryId = 1

  function pushHistory(icon: string, text: string) {
    const nowMs = Date.now()
    const dt = new Date(nowMs)
    const timeText = `${String(dt.getHours()).padStart(2, '0')}:${String(dt.getMinutes()).padStart(2, '0')}:${String(dt.getSeconds()).padStart(2, '0')}`
    history.push({ id: nextHistoryId++, icon, text, timeText, timestampMs: nowMs })
    if (history.length > MAX_HISTORY) history.splice(0, history.length - MAX_HISTORY)
  }

  // -------------------------
  // Participants (dropdown)
  // -------------------------

  const snapshotParticipants = ref<ParticipantInfo[]>([])
  const fetchedParticipants = ref<ParticipantInfo[] | null>(null)
  let participantsFetchedAtMs = 0
  let participantsFetchEpoch = 0

  const participants = computed(() => fetchedParticipants.value ?? snapshotParticipants.value)

  async function refreshParticipants(o?: { force?: boolean }) {
    // Best-effort only: dropdowns can fall back to snapshot.
    const now = Date.now()
    if (!o?.force && fetchedParticipants.value && now - participantsFetchedAtMs < 30_000) return

    const myEpoch = ++participantsFetchEpoch
    try {
      const items = await opts.actions.fetchParticipants()
      // Ignore stale result.
      if (participantsFetchEpoch !== myEpoch) return
      // Safety: don't replace snapshot-derived data with an empty list.
      if (Array.isArray(items) && items.length > 0) {
        fetchedParticipants.value = items
        participantsFetchedAtMs = now
      }
    } catch {
      // ignore (fallback on snapshot)
    }
  }

  // -------------------------
  // Trustlines (dropdown + capacity)
  // -------------------------

  const snapshotTrustlines = ref<TrustlineInfo[]>([])
  const fetchedTrustlines = ref<TrustlineInfo[] | null>(null)
  const fetchedTrustlinesEq = ref<string>('')
  let trustlinesFetchedAtMs = 0
  let trustlinesFetchEpoch = 0

  const trustlines = computed(() => {
    const eq = normalizeEq(opts.equivalent.value)
    const fetchedOk = normalizeEq(fetchedTrustlinesEq.value) === eq && fetchedTrustlines.value != null
    return (fetchedOk ? fetchedTrustlines.value : null) ?? snapshotTrustlines.value
  })

  function normalizeEq(v: unknown): string {
    return String(v ?? '').trim().toUpperCase()
  }

  function invalidateTrustlinesCache(eq?: string) {
    const curEq = normalizeEq(eq ?? opts.equivalent.value)
    if (normalizeEq(fetchedTrustlinesEq.value) !== curEq) return
    fetchedTrustlines.value = null
    fetchedTrustlinesEq.value = ''
    trustlinesFetchedAtMs = 0
  }

  async function refreshTrustlines(o?: { force?: boolean }) {
    // Best-effort only: dropdowns can fall back to snapshot.
    const eq = normalizeEq(opts.equivalent.value)
    const now = Date.now()
    const cachedForEq = normalizeEq(fetchedTrustlinesEq.value) === eq && !!fetchedTrustlines.value
    if (!o?.force && cachedForEq && now - trustlinesFetchedAtMs < 15_000) return

    const myEpoch = ++trustlinesFetchEpoch
    try {
      const items = await opts.actions.fetchTrustlines(eq)
      // Ignore stale result.
      if (trustlinesFetchEpoch !== myEpoch) return
      if (Array.isArray(items)) {
        fetchedTrustlines.value = items
        fetchedTrustlinesEq.value = eq
        trustlinesFetchedAtMs = now
      }
    } catch {
      // ignore (fallback on snapshot)
    }
  }

  function findActiveTrustline(from: string | null, to: string | null): TrustlineInfo | null {
    if (!from || !to) return null
    const items = trustlines.value
    for (const tl of items ?? []) {
      if (tl.from_pid === from && tl.to_pid === to && isActiveStatus(tl.status)) return tl
    }
    return null
  }

  watch(
    () => opts.snapshot.value,
    (snap) => {
      if (!snap) {
        snapshotParticipants.value = []
        snapshotTrustlines.value = []
        return
      }

      snapshotParticipants.value = (snap.nodes ?? []).map((n) => ({
        pid: n.id,
        name: n.name ?? n.id,
        type: n.type ?? '',
        status: n.status ?? 'active',
      }))

      const nameByPid = new Map<string, string>()
      for (const n of snap.nodes ?? []) {
        const pid = n.id.trim()
        if (!pid) continue
        const nm = (n.name ?? pid).trim() || pid
        nameByPid.set(pid, nm)
      }

      const eq = normalizeEq(snap.equivalent)
      snapshotTrustlines.value = (snap.links ?? []).map((l) => {
        const from = l.source
        const to = l.target
        return {
          from_pid: from,
          from_name: nameByPid.get(from) ?? from,
          to_pid: to,
          to_name: nameByPid.get(to) ?? to,
          equivalent: eq,
          limit: String(l.trust_limit ?? ''),
          used: String(l.used ?? ''),
          available: String(l.available ?? ''),
          status: l.status ?? 'active',
        }
      })
    },
    { immediate: true },
  )

  const availableCapacity = computed(() => {
    // Prefer backend trustlines list when present (can be more authoritative than snapshot).
    // Payment `from -> to` uses capacity of trustline `to -> from` (creditor -> debtor).
    const tl = findActiveTrustline(state.toPid, state.fromPid)
    const tlAvail = parseAmountStringOrNull(tl?.available)
    if (tlAvail != null && String(tlAvail).trim()) return tlAvail

    const l = findActiveLink(opts.snapshot.value, state.toPid, state.fromPid)
    return parseAmountStringOrNull(l?.available)
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
    const l = findActiveLink(opts.snapshot.value, state.fromPid, state.toPid)
    return !l
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

  function clearError() {
    state.error = null
  }

  function startNewFlow(phase: InteractPhase) {
    if (busyRef.value) return
    clearError()
    state.phase = phase
    state.fromPid = null
    state.toPid = null
    state.selectedEdgeKey = null

    // Best-effort prefetch for dropdown UX.
    void refreshParticipants()
  }

  function cancel() {
    // Invalidate any in-flight result (success/error) so it can't update state after cancel.
    epoch += 1
    state.phase = 'idle'
    state.fromPid = null
    state.toPid = null
    state.selectedEdgeKey = null
    state.error = null
  }

  function startPaymentFlow() {
    startNewFlow('picking-payment-from')
  }

  function startTrustlineFlow() {
    startNewFlow('picking-trustline-from')

    // Best-effort prefetch for trustline dropdowns / more up-to-date limits.
    void refreshTrustlines()
  }

  function startClearingFlow() {
    if (busyRef.value) return
    clearError()
    state.phase = 'confirm-clearing'
    state.fromPid = null
    state.toPid = null
    state.selectedEdgeKey = null

    // Clear stale results so the panel doesn't flash previous cycles.
    state.lastClearing = null
  }

  function selectNode(nodeId: string) {
    if (busyRef.value) return
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

      // Use the same trustline existence logic as dropdown setters.
      recomputeTrustlinePhase()
      return
    }
  }

  function selectEdge(edgeKey: string) {
    if (busyRef.value) return
    const parsed = parseEdgeKey(edgeKey)
    if (!parsed) return
    clearError()
    state.fromPid = parsed.from
    state.toPid = parsed.to
    state.selectedEdgeKey = keyEdge(parsed.from, parsed.to)
    state.phase = 'editing-trustline'

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
      state.phase = 'idle'
      state.fromPid = null
      state.toPid = null
      state.selectedEdgeKey = null
      state.error = null
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
    clearError()
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
    clearError()
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
    clearError()
    const from = state.fromPid
    const to = state.toPid
    await runBusy(async ({ isCurrent, resetToIdle }) => {
      if (!from || !to) throw new Error('Select trustline first')
      await opts.actions.updateTrustline(from, to, newLimit, opts.equivalent.value)
      if (!isCurrent()) return

      // BUG-5: log to history
      pushHistory('✏️', `Trustline updated: ${from} → ${to} → limit ${newLimit}`)
      invalidateTrustlinesCache(opts.equivalent.value)
      void refreshTrustlines({ force: true })
      resetToIdle()
    })
  }

  async function confirmTrustlineClose(): Promise<void> {
    clearError()
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
      return
    }
  }

  function setPaymentToPid(pid: string | null) {
    if (busyRef.value) return
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
      return
    }
  }

  function recomputeTrustlinePhase() {
    if (!state.fromPid || !state.toPid) return
    if (state.fromPid === state.toPid) return

    // Prefer fetched trustlines list when present.
    const tl = findActiveTrustline(state.fromPid, state.toPid)
    const snapLink = findActiveLink(opts.snapshot.value, state.fromPid, state.toPid)
    const has = !!tl || !!snapLink

    if (has) {
      state.selectedEdgeKey = keyEdge(state.fromPid, state.toPid)
      state.phase = 'editing-trustline'
    } else {
      state.selectedEdgeKey = null
      state.phase = 'confirm-trustline-create'
    }
  }

  function setTrustlineFromPid(pid: string | null) {
    if (busyRef.value) return
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
      return
    }
  }

  function setTrustlineToPid(pid: string | null) {
    if (busyRef.value) return
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
        return
      }
      recomputeTrustlinePhase()
      return
    }
  }

  function selectTrustline(fromPid: string, toPid: string) {
    if (busyRef.value) return
    clearError()
    state.fromPid = String(fromPid ?? '').trim() || null
    state.toPid = String(toPid ?? '').trim() || null
    if (!state.fromPid || !state.toPid) return
    state.selectedEdgeKey = keyEdge(state.fromPid, state.toPid)
    state.phase = 'editing-trustline'
  }

  // Keep trustlines cache keyed by equivalent.
  watch(
    () => normalizeEq(opts.equivalent.value),
    () => {
      // Switching EQ changes the trustlines list semantics.
      // Clear immediately so UI can't show stale trustlines while fetch is in-flight.
      fetchedTrustlines.value = null
      fetchedTrustlinesEq.value = ''
      trustlinesFetchedAtMs = 0
      void refreshTrustlines()
    },
    { immediate: true },
  )

  async function confirmClearing(): Promise<void> {
    clearError()
    await runBusy(async ({ isCurrent, resetToIdle }) => {
      // Two-phase: preview (store cycles) -> running (FX animation) -> idle.
      state.phase = 'clearing-preview'
      state.lastClearing = null

      const res = await opts.actions.runClearing(opts.equivalent.value)
      if (!isCurrent()) return
      state.lastClearing = res

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

      state.phase = 'clearing-running'

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
    availableCapacity,
    availableTargetIds,

    busy,
    canSendPayment,
    canCreateTrustline,

    setPaymentFromPid,
    setPaymentToPid,
    setTrustlineFromPid,
    setTrustlineToPid,
    selectTrustline,

    history,
  }
}

