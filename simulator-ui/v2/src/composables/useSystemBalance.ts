import { computed, type ComputedRef, type Ref } from 'vue'

import type { GraphSnapshot } from '../types'
import { parseAmountNumber } from '../utils/amount'
import { isActiveStatus } from '../utils/status'

export type SystemBalance = {
  totalUsed: number
  totalAvailable: number
  activeTrustlines: number
  activeParticipants: number
  utilization: number
  isClean: boolean
}

export function useSystemBalance(snapshot: Ref<GraphSnapshot | null>): {
  balance: ComputedRef<SystemBalance>
} {
  const balance = computed<SystemBalance>(() => {
    const snap = snapshot.value
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

