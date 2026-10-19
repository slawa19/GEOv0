import { toRaw } from 'vue'

import { canonicalEquivalent, sameEquivalent } from '../config/equivalents'
import type { GraphLink, GraphSnapshot } from '../types'
import { addMoney, compareMoney, moneyText } from '../utils/money'

/**
 * 039 slice D (T3940): the total debt "was -> now" after a debt-changing event (spec 039, target 6, amendment P-5).
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
 * The "was" the bar may state for the shown totals, or `null`. Stated only while the pair belongs to what the bar
 * shows: the same snapshot object (a reload, an incremental refresh, a switch of equivalent, scenario or run replaces
 * it), the same equivalent, a complete total, and a "now" that IS the current total (a change by any other path drops
 * the pair instead of leaving a stale "was" beside a new figure).
 */
export function shownDebtBefore(
  change: DebtChange | null | undefined,
  snapshot: GraphSnapshot | null | undefined,
  total: { total: string; complete: boolean },
): string | null {
  if (!change || !snapshot) return null
  if (toRaw(change.snapshot) !== toRaw(snapshot)) return null
  if (!sameEquivalent(change.equivalent, snapshot.equivalent)) return null
  if (!total.complete || compareMoney(change.after, total.total) !== 0) return null
  return change.before
}
