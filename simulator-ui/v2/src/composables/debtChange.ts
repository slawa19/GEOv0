import { toRaw } from 'vue'

import { canonicalEquivalent, sameEquivalent } from '../config/equivalents'
import type { GraphLink, GraphSnapshot } from '../types'
import { addMoney, compareMoney, moneyText } from '../utils/money'

/**
 * 039 slice D (T3940): the total debt "was -> now" of the last CLEARING (spec 039 :160, target 6, amendment P-5).
 * Only `clearing.done` states it; payments and topology events neither create, replace nor drop it (advisor's
 * decision 2026-10-19, option (a): the label means "the clearing took this much off"). A clearing that changes no
 * debt drops it.
 *
 * The two totals are exact sums of the lines of ONE snapshot, taken immediately before and after the patches of ONE
 * accepted event (`realEventPipeline.ts`). Never `now + cleared_amount` (a cycle of three lines cleared by 10 lowers
 * the total by 30), never the value the bar happened to display (`useSystemBalance.ts` is debounced on a replaced
 * snapshot). No SSE extension: `clearing.done` carries no totals; the lines do.
 */
export type DebtChange = {
  before: string
  after: string
  /** The equivalent of the snapshot both totals were taken on. */
  equivalent: string
  eventId: string
  /** The snapshot object both totals were taken on: the pair is stated only while this very object is shown. */
  snapshot: GraphSnapshot
}

/**
 * The exact total debt of a snapshot's lines: every line, whatever its status (034 S8, F-034-D1 - freezing is not
 * repayment). `complete: false` when a line's debt is not an amount: `total` is then the sum of the known lines only
 * (what the bar has always shown), and it is not a total a "was -> now" may be built on - unknown is not zero.
 */
export function totalDebtOf(links: readonly GraphLink[] | null | undefined): { total: string; complete: boolean } {
  let total = '0'
  let complete = true
  for (const l of links ?? []) {
    if (moneyText(l.used) === null) complete = false
    total = addMoney(total, l.used)
  }
  return { total, complete }
}

/**
 * Takes the exact total of `snapshot` now and returns the step that closes the pair once the changes of ONE event are
 * applied: `null` when there is no snapshot, a line's debt is unknown before or after, or the event changed no debt.
 */
export function startDebtChange(snapshot: GraphSnapshot | null, eventId: string): () => DebtChange | null {
  const before = snapshot ? totalDebtOf(snapshot.links) : null
  return () => {
    if (!snapshot || !before) return null
    const after = totalDebtOf(snapshot.links)
    if (!before.complete || !after.complete) return null
    if (compareMoney(before.total, after.total) === 0) return null
    return { before: before.total, after: after.total, equivalent: canonicalEquivalent(snapshot.equivalent), eventId, snapshot }
  }
}

/** `startDebtChange` around `apply` (the patches of one event). */
export function debtChangeAround(snapshot: GraphSnapshot | null, eventId: string, apply: () => void): DebtChange | null {
  const finish = startDebtChange(snapshot, eventId)
  apply()
  return finish()
}

/**
 * The clearing's "was -> now" the bar may state, or `null`. Stated only while the pair belongs to what the bar shows:
 * the same snapshot object (a reload, an incremental refresh, a switch of equivalent, scenario or run replaces it) and
 * the same equivalent.
 *
 * Deliberately NOT required (changed 2026-10-19 with the advisor's decision (a)): that the pair's "now" equals the
 * current total. Payments after the clearing move the bar's total and leave the pair - which is the clearing's own
 * before and after, a statement about what the clearing did, true whatever came after it on the same snapshot.
 */
export function shownClearingChange(
  change: DebtChange | null | undefined,
  snapshot: GraphSnapshot | null | undefined,
): { before: string; after: string } | null {
  if (!change || !snapshot) return null
  if (toRaw(change.snapshot) !== toRaw(snapshot)) return null
  if (!sameEquivalent(change.equivalent, snapshot.equivalent)) return null
  return { before: change.before, after: change.after }
}
