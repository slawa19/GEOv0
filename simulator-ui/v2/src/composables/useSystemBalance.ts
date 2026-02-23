import { computed, type ComputedRef, getCurrentScope, onScopeDispose, type Ref, shallowRef, watch } from 'vue'

import type { GraphSnapshot } from '../types'
import { parseAmountNumber } from '../utils/numberFormat'
import { isActiveStatus } from '../utils/status'

export type SystemBalance = {
  totalUsed: number
  totalAvailable: number
  activeTrustlines: number
  activeParticipants: number
  utilization: number
  isClean: boolean
}

// TD-3: debounce interval for balance recomputation.
// With >1000 edges and high SSE-patch frequency, the O(N) summation would run
// on every snapshot update. The debounced shallowRef ensures recomputation
// happens at most once per ~100 ms even under intensive server-side ticking.
const BALANCE_DEBOUNCE_MS = 100

export function useSystemBalance(snapshot: Ref<GraphSnapshot | null>): {
  balance: ComputedRef<SystemBalance>
} {
  // Debounced copy of snapshot. Vue tracks only the shallowRef itself, not the
  // deep structure of GraphSnapshot, so computed(balance) re-runs only when this
  // ref is actually replaced — after the debounce timer fires.
  const debouncedSnap = shallowRef<GraphSnapshot | null>(snapshot.value)
  let debounceTimer: ReturnType<typeof setTimeout> | null = null

  const stopWatch = watch(snapshot, (snap) => {
    if (debounceTimer !== null) clearTimeout(debounceTimer)
    debounceTimer = setTimeout(() => {
      debounceTimer = null
      debouncedSnap.value = snap
    }, BALANCE_DEBOUNCE_MS)
  })

  if (getCurrentScope()) {
    onScopeDispose(() => {
      stopWatch()
      if (debounceTimer !== null) clearTimeout(debounceTimer)
    })
  }

  const balance = computed<SystemBalance>(() => {
    const snap = debouncedSnap.value
    if (!snap) {
      return {
        totalUsed: 0,
        totalAvailable: 0,
        activeTrustlines: 0,
        activeParticipants: 0,
        utilization: 0,
        isClean: true,
      }
    }

    let totalUsed = 0
    let totalAvailable = 0
    let activeTrustlines = 0

    for (const l of snap.links ?? []) {
      if (!isActiveStatus((l as any)?.status)) continue
      activeTrustlines += 1
      totalUsed += parseAmountNumber((l as any)?.used)
      totalAvailable += parseAmountNumber((l as any)?.available)
    }

    let activeParticipants = 0
    for (const n of snap.nodes ?? []) {
      if (isActiveStatus((n as any)?.status)) activeParticipants += 1
    }

    const denom = totalUsed + totalAvailable
    const utilization = denom > 0 ? totalUsed / denom : 0

    return {
      totalUsed,
      totalAvailable,
      activeTrustlines,
      activeParticipants,
      utilization: Number.isFinite(utilization) ? utilization : 0,
      isClean: totalUsed <= 0,
    }
  })

  return { balance }
}

