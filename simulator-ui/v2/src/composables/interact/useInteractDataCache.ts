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
  equivalent: Ref<string>
  snapshot: Ref<GraphSnapshot | null>
  parseAmountStringOrNull: (v: unknown) => string | null
}): {
  participants: ComputedRef<ParticipantInfo[]>
  trustlines: ComputedRef<TrustlineInfo[]>
  trustlinesLoading: ComputedRef<boolean>
  refreshParticipants: (o?: { force?: boolean }) => Promise<void>
  refreshTrustlines: (o?: { force?: boolean }) => Promise<void>
  invalidateTrustlinesCache: (eq?: string) => void
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
      }
    } catch {
      // ignore (fallback on snapshot)
    } finally {
      trustlinesLoadingCount = Math.max(0, trustlinesLoadingCount - 1)
      trustlinesLoadingRef.value = trustlinesLoadingCount > 0
    }
  }

  const trustlinesLoading = computed(() => trustlinesLoadingRef.value)

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
        return {
          from_pid: from,
          from_name: nameByPid.get(from) ?? from,
          to_pid: to,
          to_name: nameByPid.get(to) ?? to,
          equivalent: eq,
          limit: opts.parseAmountStringOrNull(l.trust_limit) ?? '',
          used: opts.parseAmountStringOrNull(l.used) ?? '',
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
    refreshParticipants,
    refreshTrustlines,
    invalidateTrustlinesCache,
    findActiveTrustline,
  }
}
