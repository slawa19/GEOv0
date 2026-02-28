import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'

import type { GraphSnapshot } from '../../types'
import { isActiveStatus } from '../../utils/status'
import type { ParticipantInfo, TrustlineInfo } from '../../api/simulatorTypes'
import type { useInteractActions } from '../useInteractActions'

function normalizeEq(v: unknown): string {
  return String(v ?? '').trim().toUpperCase()
}

export function useInteractDataCache(opts: {
  actions: ReturnType<typeof useInteractActions>
  runId: Ref<string>
  equivalent: Ref<string>
  snapshot: Ref<GraphSnapshot | null>
  parseAmountStringOrNull: (v: unknown) => string | null
}): {
  participants: ComputedRef<ParticipantInfo[]>
  trustlines: ComputedRef<TrustlineInfo[]>
  trustlinesLoading: ComputedRef<boolean>
  trustlinesLastError: ComputedRef<string | null>
  refreshParticipants: (o?: { force?: boolean }) => Promise<void>
  refreshTrustlines: (o?: { force?: boolean }) => Promise<void>
  /** Phase 2.5: backend-first reachable payment targets (multi-hop). */
  refreshPaymentTargets: (o: { fromPid: string; maxHops: number; force?: boolean }) => Promise<void>
  /** Cache (by run+eq+from+maxHops) for To dropdown filtering. */
  paymentTargetsByKey: Ref<Map<string, Set<string>>>
  /** Loading flags per key. Key must match paymentTargetsByKey key. */
  paymentTargetsLoadingByKey: Ref<Map<string, boolean>>
  paymentTargetsLastError: ComputedRef<string | null>
  paymentTargetsKey: (o: { runId: string; eq: string; fromPid: string; maxHops: number }) => string
  invalidateTrustlinesCache: (eq?: string) => void
  patchTrustlineLimitLocal: (from: string, to: string, newLimit: string, eq?: string) => void
  findActiveTrustline: (from: string | null, to: string | null) => TrustlineInfo | null
} {
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

  const trustlinesLoadingRef = ref(false)
  let trustlinesLoadingCount = 0

  const trustlinesLastErrorRef = ref<string | null>(null)

  const trustlines = computed(() => {
    const eq = normalizeEq(opts.equivalent.value)
    const fetchedOk = normalizeEq(fetchedTrustlinesEq.value) === eq && fetchedTrustlines.value != null
    return (fetchedOk ? fetchedTrustlines.value : null) ?? snapshotTrustlines.value
  })

  function invalidateTrustlinesCache(eq?: string) {
    const curEq = normalizeEq(eq ?? opts.equivalent.value)
    if (normalizeEq(fetchedTrustlinesEq.value) !== curEq) return
    fetchedTrustlines.value = null
    fetchedTrustlinesEq.value = ''
    trustlinesFetchedAtMs = 0
  }

  function normalizeAmount(v: string): string {
    const s = String(v ?? '').trim()
    return opts.parseAmountStringOrNull(s) ?? s
  }

  function recomputeAvailable(used: string | null | undefined, limit: string): string | null {
    const usedNum = Number(used ?? NaN)
    const limitNum = Number(limit ?? NaN)
    if (!Number.isFinite(usedNum) || !Number.isFinite(limitNum)) return null
    return opts.parseAmountStringOrNull(limitNum - usedNum)
  }

  function patchList(items: TrustlineInfo[] | null, from: string, to: string, limit: string): TrustlineInfo[] | null {
    if (!Array.isArray(items) || items.length === 0) return items
    let changed = false
    const next = items.map((tl) => {
      if (tl.from_pid !== from || tl.to_pid !== to) return tl
      const available = recomputeAvailable(tl.used ?? null, limit)
      changed = true
      return {
        ...tl,
        limit,
        ...(available != null ? { available } : {}),
      }
    })
    return changed ? next : items
  }

  /**
   * Best-effort optimistic patch so UI reflects the new limit immediately.
   * Also helps when `fetchTrustlines` fails (it is intentionally swallowed).
   */
  function patchTrustlineLimitLocal(from: string, to: string, newLimit: string, eq?: string) {
    const fromPid = String(from ?? '').trim()
    const toPid = String(to ?? '').trim()
    if (!fromPid || !toPid) return

    const targetEq = normalizeEq(eq ?? opts.equivalent.value)
    const limit = normalizeAmount(newLimit)
    if (!limit) return

    // Patch fetched list only if it's for the active equivalent.
    if (normalizeEq(fetchedTrustlinesEq.value) === targetEq) {
      fetchedTrustlines.value = patchList(fetchedTrustlines.value, fromPid, toPid, limit)
    }

    // Patch snapshot-derived list (used as fallback).
    snapshotTrustlines.value = (patchList(snapshotTrustlines.value, fromPid, toPid, limit) ?? snapshotTrustlines.value) as TrustlineInfo[]
  }

  async function refreshTrustlines(o?: { force?: boolean }) {
    // Best-effort only: dropdowns can fall back to snapshot.
    const eq = normalizeEq(opts.equivalent.value)
    const now = Date.now()
    const cachedForEq = normalizeEq(fetchedTrustlinesEq.value) === eq && !!fetchedTrustlines.value
    if (!o?.force && cachedForEq && now - trustlinesFetchedAtMs < 15_000) return

    trustlinesLoadingCount += 1
    trustlinesLoadingRef.value = true

    const myEpoch = ++trustlinesFetchEpoch
    try {
      const items = await opts.actions.fetchTrustlines(eq)
      // Ignore stale result.
      if (trustlinesFetchEpoch !== myEpoch) return
      if (Array.isArray(items)) {
        fetchedTrustlines.value = items
        fetchedTrustlinesEq.value = eq
        trustlinesFetchedAtMs = now
        trustlinesLastErrorRef.value = null
      }
    } catch (e: any) {
      // Best-effort: fall back on snapshot-derived trustlines.
      // Keep a lightweight error signal for UI hints/debugging.
      trustlinesLastErrorRef.value = String(e?.message ?? e ?? 'Trustlines refresh failed')
    } finally {
      trustlinesLoadingCount = Math.max(0, trustlinesLoadingCount - 1)
      trustlinesLoadingRef.value = trustlinesLoadingCount > 0
    }
  }

  const trustlinesLoading = computed(() => trustlinesLoadingRef.value)
  const trustlinesLastError = computed(() => trustlinesLastErrorRef.value)

  // -------------------------
  // Payment targets (To dropdown filtering)
  // -------------------------

  function normalizeRunId(v: unknown): string {
    return String(v ?? '').trim()
  }

  function normalizePid(v: unknown): string {
    return String(v ?? '').trim()
  }

  function paymentTargetsKey(o: { runId: string; eq: string; fromPid: string; maxHops: number }): string {
    const runId = normalizeRunId(o.runId)
    const eq = normalizeEq(o.eq)
    const fromPid = normalizePid(o.fromPid)
    const maxHops = Number(o.maxHops)
    return `${runId}::${eq}::${fromPid}::${Number.isFinite(maxHops) ? String(maxHops) : 'NaN'}`
  }

  // NOTE: keep Maps in refs and replace on update so consumers can depend on ref identity.
  const paymentTargetsByKey = ref(new Map<string, Set<string>>())
  const paymentTargetsLoadingByKey = ref(new Map<string, boolean>())
  const paymentTargetsLastErrorRef = ref<string | null>(null)
  const paymentTargetsLastError = computed(() => paymentTargetsLastErrorRef.value)
  const paymentTargetsFetchEpochByKey = new Map<string, number>()
  const paymentTargetsFetchedAtMsByKey = new Map<string, number>()

  // Prevent “forever stale” targets when the underlying graph changes over time.
  // Must be aligned with other dropdown caches in this file (participants=30s, trustlines=15s).
  const PAYMENT_TARGETS_TTL_MS = 10_000

  function setPaymentTargetsLoading(key: string, loading: boolean) {
    const next = new Map(paymentTargetsLoadingByKey.value)
    if (loading) next.set(key, true)
    else next.delete(key)
    paymentTargetsLoadingByKey.value = next
  }

  async function refreshPaymentTargets(o: { fromPid: string; maxHops: number; force?: boolean }) {
    const runId = normalizeRunId(opts.runId.value)
    const eq = normalizeEq(opts.equivalent.value)
    const fromPid = normalizePid(o.fromPid)
    const maxHops = Number(o.maxHops)
    if (!runId || !eq || !fromPid || !Number.isFinite(maxHops) || !(maxHops >= 1)) return

    const key = paymentTargetsKey({ runId, eq, fromPid, maxHops })
    const now = Date.now()
    const cached = paymentTargetsByKey.value.get(key)

    // TTL: allow reuse for a short time, but revalidate periodically.
    if (!o.force && cached) {
      const fetchedAt = paymentTargetsFetchedAtMsByKey.get(key) ?? 0
      if (fetchedAt > 0 && now - fetchedAt < PAYMENT_TARGETS_TTL_MS) return
    }

    setPaymentTargetsLoading(key, true)
    const myEpoch = (paymentTargetsFetchEpochByKey.get(key) ?? 0) + 1
    paymentTargetsFetchEpochByKey.set(key, myEpoch)

    try {
      const items = await opts.actions.fetchPaymentTargets(eq, fromPid, maxHops)
      // Ignore stale result for the same key.
      if (paymentTargetsFetchEpochByKey.get(key) !== myEpoch) return

      const ids = new Set<string>()
      for (const it of items ?? []) {
        const pid = normalizePid((it as any)?.to_pid)
        if (!pid) continue
        ids.add(pid)
      }

      const next = new Map(paymentTargetsByKey.value)
      next.set(key, ids)
      paymentTargetsByKey.value = next
      paymentTargetsLastErrorRef.value = null
      paymentTargetsFetchedAtMsByKey.set(key, now)
    } catch (e: any) {
      // Ignore stale error for the same key.
      if (paymentTargetsFetchEpochByKey.get(key) !== myEpoch) return

      // Best-effort: treat as known-empty (so UI does not crash / is deterministic).
      const next = new Map(paymentTargetsByKey.value)
      next.set(key, new Set())
      paymentTargetsByKey.value = next
      paymentTargetsLastErrorRef.value = String(e?.message ?? e ?? 'Payment targets refresh failed')
      // Treat error response as “known” for UI determinism, but still revalidate after TTL.
      paymentTargetsFetchedAtMsByKey.set(key, now)
    } finally {
      // Only clear if this fetch is still the latest for the key.
      if (paymentTargetsFetchEpochByKey.get(key) === myEpoch) {
        setPaymentTargetsLoading(key, false)
      }
    }
  }

  // Run/EQ change invalidates payment targets semantics.
  watch(
    () => `${normalizeRunId(opts.runId.value)}::${normalizeEq(opts.equivalent.value)}`,
    () => {
      paymentTargetsByKey.value = new Map()
      paymentTargetsLoadingByKey.value = new Map()
      paymentTargetsLastErrorRef.value = null
      paymentTargetsFetchEpochByKey.clear()
      paymentTargetsFetchedAtMsByKey.clear()
    },
    { immediate: true },
  )

  // Snapshot change implies the underlying graph semantics may have changed.
  // Keep behavior deterministic: clear payment-targets cache so consumers can re-fetch.
  watch(
    () => String(opts.snapshot.value?.generated_at ?? ''),
    () => {
      paymentTargetsByKey.value = new Map()
      paymentTargetsLoadingByKey.value = new Map()
      paymentTargetsLastErrorRef.value = null
      paymentTargetsFetchEpochByKey.clear()
      paymentTargetsFetchedAtMsByKey.clear()
    },
    { immediate: false },
  )

  function findActiveTrustline(from: string | null, to: string | null): TrustlineInfo | null {
    if (!from || !to) return null
    const items = trustlines.value
    for (const tl of items ?? []) {
      if (tl.from_pid === from && tl.to_pid === to && isActiveStatus(tl.status)) return tl
    }
    return null
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

        // NOTE(14.7): Snapshot fallback for trustlines is best-effort.
        // - Backend `/graph/snapshot` (and demo fixtures) currently don't provide `reverse_used`,
        //   so close-guard (used/reverse_used > 0) may be a false-negative until the trustlines-list API fetch.
        // - If `reverse_used` is ever added to snapshot.links, we map it through here.
        // - Backend 409 remains the last barrier for closing a trustline with outstanding debt.
        const reverseUsed = opts.parseAmountStringOrNull(l.reverse_used)
        return {
          from_pid: from,
          from_name: nameByPid.get(from) ?? from,
          to_pid: to,
          to_name: nameByPid.get(to) ?? to,
          equivalent: eq,
          limit: opts.parseAmountStringOrNull(l.trust_limit) ?? '',
          used: opts.parseAmountStringOrNull(l.used) ?? '',
          ...(reverseUsed != null ? { reverse_used: reverseUsed } : {}),
          available: opts.parseAmountStringOrNull(l.available) ?? '',
          status: l.status ?? 'active',
        }
      })
    },
    { immediate: true },
  )

  return {
    participants,
    trustlines,
    trustlinesLoading,
    trustlinesLastError,
    refreshParticipants,
    refreshTrustlines,
    refreshPaymentTargets,
    paymentTargetsByKey,
    paymentTargetsLoadingByKey,
    paymentTargetsLastError,
    paymentTargetsKey,
    invalidateTrustlinesCache,
    patchTrustlineLimitLocal,
    findActiveTrustline,
  }
}
