import { computed, type ComputedRef, getCurrentScope, onScopeDispose, type Ref, shallowRef, toRaw, watch } from 'vue'

import type { GraphSnapshot } from '../types'
import { addMoney, moneyText } from '../utils/money'
import { isActiveStatus } from '../utils/status'
import { canonicalEquivalent } from '../config/equivalents'
import { shownClearingChange, totalDebtOf, type DebtChange } from './debtChange'

/** 028 F-028-48: the totals are money TEXT of the snapshot's `equivalent` (exact sums, no float). */
export type SystemBalance = {
  totalUsed: string
  totalAvailable: string
  equivalent: string
  activeTrustlines: number
  activeParticipants: number
  utilization: number
  /**
   * 039 slice D: the total debt just before and just after the last CLEARING on the shown snapshot (`debtChange.ts`,
   * `shownClearingChange`); `null` - none. Its `after` is the clearing's own and stays while payments move `totalUsed`.
   */
  clearingChange: { before: string; after: string } | null
  // 048 S1 (decision 4): no `isClean` - its only reader, the "Clean" badge of `SystemBalanceBar`, is gone (it repeated
  // "Общий долг 0.00"); a clean or frozen debt is read from `totalUsed`.
}

// TD-3: debounce interval for a REPLACED snapshot (a scene load, a refresh, a switch of equivalent or run) - and only
// for it. 054 B-12: it does not bound the recomputation under SSE patches. The patches mutate the shown snapshot in place
// (`demo/patches.ts` applyEdgePatches, the topology filter/push of `realEventPipeline.ts`); the watch below is not deep,
// so it does not fire on them, and the object `debouncedSnap` holds is the snapshot's reactive proxy, so `balance` reads
// the links through it and is invalidated by each patch at once. What bounds it is that a computed is lazy: N patches
// before one read cost one O(N links) sum, and its readers are the renders of `SystemBalanceBar`
// (`SimulatorAppRoot.vue`), once per flush. The totals following a patch at once is relied on (`debtChange.p039d.test.ts`,
// "a topology patch that moves a debt does not replace it either": a `toRaw` copy here turns it red); bounding the
// patches too would make a total lag ~100 ms behind its patch.
const BALANCE_DEBOUNCE_MS = 100

export function useSystemBalance(
  snapshot: Ref<GraphSnapshot | null>,
  /** 039 slice D: the pair of totals around the last clearing (`SimulatorAppState.debtChange`). */
  debtChange?: () => DebtChange | null | undefined,
): {
  balance: ComputedRef<SystemBalance>
} {
  // Debounced copy of the snapshot REFERENCE: a replacement reaches the totals after the timer fires. The value is the
  // reactive proxy of the shown snapshot, so an in-place patch reaches them at once (see TD-3 above).
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
        totalUsed: '0',
        totalAvailable: '0',
        equivalent: '',
        activeTrustlines: 0,
        activeParticipants: 0,
        utilization: 0,
        clearingChange: null,
      }
    }

    // 034 S8 (F-034-D1): a debt exists whatever the state of its line - freezing is not repayment. One sum for the
    // bar and for the "was -> now" of slice D (`totalDebtOf`), so the two cannot disagree.
    const debt = totalDebtOf(snap.links)
    const totalUsed = debt.total
    let totalAvailable = '0'
    let usableUsed = '0'
    let activeTrustlines = 0

    for (const l of snap.links ?? []) {
      // Only the capacity and the count below belong to usable lines (the total debt is above).
      if (!isActiveStatus(l.status)) continue
      activeTrustlines += 1
      usableUsed = addMoney(usableUsed, l.used)
      // 026 `T2602`: a negative `available` is the debt's excess over a lowered limit, not negative capacity;
      // it adds nothing here (its debt is already in `totalUsed`).
      const available = moneyText(l.available)
      if (available !== null && !available.startsWith('-')) totalAvailable = addMoney(totalAvailable, available)
    }

    let activeParticipants = 0
    for (const n of snap.nodes ?? []) {
      if (isActiveStatus(n.status)) activeParticipants += 1
    }

    // A share, not money: the exact totals are only converted for the ratio. One base for both parts - the usable
    // lines: the debt of a frozen line is in Total Debt, but not over a capacity that line is not part of.
    const denom = Number(addMoney(usableUsed, totalAvailable))
    const utilization = denom > 0 ? Number(usableUsed) / denom : 0

    return {
      totalUsed,
      totalAvailable,
      equivalent: canonicalEquivalent(snap.equivalent),
      activeTrustlines,
      activeParticipants,
      utilization: Number.isFinite(utilization) ? utilization : 0,
      // 039 slice D, §15 Codex 2026-10-19 (P2): the pair must belong to the snapshot shown NOW as well as to the one
      // these (debounced) totals come from. Compared with the debounced object alone, the old "was" stayed ~100 ms
      // beside the old total after `applySceneLoad` replaced the snapshot (a refresh, a switch of equivalent or run).
      // Reading `snapshot.value` here also recomputes at once on a replacement; the totals keep their debounce.
      clearingChange: toRaw(snapshot.value) === toRaw(snap) ? shownClearingChange(debtChange?.(), snap) : null,
    }
  })

  return { balance }
}

