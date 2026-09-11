import type { GraphRebuildOptions, LabelMode } from '../../composables/useGraphVisualization'
import { formatMoneyByEquivalent } from '../../composables/useEquivalentPrecision'

export async function waitForLatestPendingGraphLoad(
  getPending: () => Promise<unknown> | null,
): Promise<void> {
  while (getPending()) {
    const pending = getPending()
    if (!pending) return
    await pending
    if (getPending() === pending) return
  }
}

export function makeMetricsKey(pid: string, eqCode: string | null, threshold: string): string {
  const p = String(pid || '').trim()
  const eq = String(eqCode || 'ALL').trim() || 'ALL'
  const thr = String(threshold || '').trim()
  return `${p}|${eq}|thr=${thr}`
}

/**
 * Денежная ячейка графа (F-012-7).
 *
 * Раньше здесь стояло `formatDecimalFixed(v, 2)`: два знака для любой величины. Это отменяло уже
 * верную точность, с которой аналитика графа отдаёт свои строки (`atomsToDecimal(…, precision)`
 * в `useGraphAnalytics`), и приписывало её же величинам эквивалента с `precision: 1`.
 * Точность обязан назвать вызывающий — кодом эквивалента строки, а не местом вывода.
 */
export function money(
  value: string,
  equivalent: unknown,
  precisionByEq: ReadonlyMap<string, number>,
): string {
  return formatMoneyByEquivalent(value, equivalent, precisionByEq)
}

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n))
}

export function pct(x: number, digits = 0): string {
  if (!Number.isFinite(x)) return '0%'
  const p = clamp(x * 100, 0, 100)
  return `${p.toFixed(digits)}%`
}

export function atomsToDecimal(atoms: bigint, precision: number): string {
  const neg = atoms < 0n
  const abs = neg ? -atoms : atoms
  const s = abs.toString()
  if (precision <= 0) return (neg ? '-' : '') + s
  const pad = precision + 1
  const padded = s.length >= pad ? s : '0'.repeat(pad - s.length) + s
  const head = padded.slice(0, padded.length - precision)
  const frac = padded.slice(padded.length - precision)
  return (neg ? '-' : '') + head + '.' + frac
}

export function extractPidFromText(text: string): string | null {
  const m = String(text || '').match(/PID_[A-Za-z0-9]+_[A-Za-z0-9]+/)
  return m ? m[0] : null
}

export type LabelPart = 'name' | 'pid'

export function labelPartsToMode(parts: LabelPart[]): LabelMode {
  const s = new Set(parts || [])
  if (s.size === 0) return 'off'
  if (s.has('name') && s.has('pid')) return 'both'
  if (s.has('pid')) return 'pid'
  return 'name'
}

export function modeToLabelParts(mode: LabelMode): LabelPart[] {
  if (mode === 'both') return ['name', 'pid']
  if (mode === 'pid') return ['pid']
  if (mode === 'name') return ['name']
  return []
}

type SeedParticipantLike = {
  display_name?: string | null
}

export function computeSeedLabel(participants: SeedParticipantLike[] | null | undefined): string {
  const n = (participants || []).length
  const first = String(participants?.[0]?.display_name || '').toLowerCase()
  if (!n) return 'Seed: (not loaded)'

  if (n === 100 && first.includes('greenfield')) return 'Seed: Greenfield (100)'
  if (n === 50 && first.includes('riverside')) return 'Seed: Riverside (50)'

  // Fallback: still useful when experimenting with custom seeds.
  const prefix = first ? `, first: ${participants?.[0]?.display_name}` : ''
  return `Seed: ${n} participants${prefix}`
}

export function graphElementOptionsForSearch<T extends { key: string; label: string }>(options: {
  guarded: boolean
  query: string
  guardedQueryMin: number
  guardedLimit: number
  buildOptions: () => T[]
}): T[] {
  const query = String(options.query || '').trim().toLocaleLowerCase()
  if (options.guarded && query.length < options.guardedQueryMin) return []

  const built = options.buildOptions()
  const matches = query
    ? built.filter((option) => `${option.label}\n${option.key}`.toLocaleLowerCase().includes(query))
    : built
  return options.guarded ? matches.slice(0, options.guardedLimit) : matches
}

export function createDebouncedGraphElementSearch<T extends { key: string; label: string }>(options: {
  delayMs: number
  guardedQueryMin: number
  guardedLimit: number
  buildOptions: () => T[]
  publish: (options: T[]) => void
}) {
  let timer: number | null = null

  function cancel() {
    if (timer === null) return
    window.clearTimeout(timer)
    timer = null
  }

  function invalidate() {
    cancel()
    options.publish([])
  }

  function search(query: string) {
    invalidate()
    if (String(query || '').trim().length < options.guardedQueryMin) return
    timer = window.setTimeout(() => {
      timer = null
      options.publish(graphElementOptionsForSearch({
        guarded: true,
        query,
        guardedQueryMin: options.guardedQueryMin,
        guardedLimit: options.guardedLimit,
        buildOptions: options.buildOptions,
      }))
    }, options.delayMs)
  }

  return { search, cancel, invalidate }
}

export type GuardedGraphSearchCacheAction = 'search' | 'invalidate' | 'none'

export function guardedGraphSearchCacheAction(
  guarded: boolean,
  wasGuarded: boolean,
): GuardedGraphSearchCacheAction {
  if (guarded && !wasGuarded) return 'search'
  if (wasGuarded) return 'invalidate'
  return 'none'
}

export async function reloadGraphView(options: {
  loadData: () => Promise<boolean>
  isCurrent: () => boolean
  afterLoad: () => Promise<void>
  applyView: (options: GraphRebuildOptions) => boolean
  rebuildOptions: GraphRebuildOptions
}): Promise<boolean> {
  if (!await options.loadData()) return false
  if (!options.isCurrent()) return false
  await options.afterLoad()
  if (!options.isCurrent()) return false
  return options.applyView(options.rebuildOptions)
}

export function syncGraphCoreForView(options: {
  guarded: boolean
  hasCore: () => boolean
  initialize: () => void
  destroy: () => void
  rebuild: (options: GraphRebuildOptions) => void
  rebuildOptions: GraphRebuildOptions
}): boolean {
  if (options.guarded) {
    if (options.hasCore()) options.destroy()
    return false
  }
  if (!options.hasCore()) options.initialize()
  if (!options.hasCore()) return false
  options.rebuild(options.rebuildOptions)
  return true
}

export type FocusModeQuery = {
  pid: string
  depth: 1 | 2
  equivalent?: string
  status?: string[]
  participant_pid: string
}

export function buildFocusModeQuery(input: {
  enabled: boolean
  rootPid: unknown
  depth: unknown
  equivalent: unknown
  statusFilter: unknown
}): FocusModeQuery | null {
  if (!input.enabled) return null

  const pid = String(input.rootPid || '').trim()
  if (!pid) return null

  const depthRaw = Number(input.depth)
  const depth = depthRaw === 2 ? 2 : 1

  const eqRaw = String(input.equivalent || '').trim()
  const equivalent = eqRaw && eqRaw.toUpperCase() !== 'ALL' ? eqRaw : undefined

  const status = Array.isArray(input.statusFilter)
    ? input.statusFilter
        .map((s) => String(s || '').trim())
        .filter(Boolean)
    : undefined

  return {
    pid,
    depth,
    ...(equivalent ? { equivalent } : {}),
    ...(status && status.length ? { status } : {}),
    participant_pid: pid,
  }
}

// F-013-1 / T1302, corrected by the internal review of programme 013 (F-013-R1/R2).
//
// A counter on the Activity card is derived from ONE optional collection, and the response says
// per collection what it carried. Three states have to survive the trip to the pixels:
//
//   * we were not told about the collection -> "—" per window. A zero here is an assertion the
//     client is not entitled to make.
//   * we were told, and it is what it is    -> the plain number. Zero included: a measured zero is
//     a fact and must read as one.
//   * we were told a PREFIX of a longer list -> a leading "≥", because every count over a cut list
//     is a lower bound.
//
// `incompleteWindows` is the fourth case and it collapses into the first - but only for the cells
// it actually reaches. Rows arrived that carry nothing able to place this participant in them, so a
// count taken without them is an undercount wearing the typography of a total.
//
// WHY A LIST OF WINDOWS AND NOT A BOOLEAN (external review of 013). The flag that stood here was
// one boolean for the whole `transactions` collection, and both the payment cell and the clearing
// cell read it. So a single committed payment with an unpublished recipient blanked a clearing zero
// that had been counted to zero independently of it - and blanked it in all three windows, one of
// which the doubtful row was too old to belong to. That is ignorance the system does not have,
// manufactured by a fix against ignorance. A doubt is now recorded at the granularity the operator
// reads it at: which counter, and which windows of it.
//
// WHY A PER-COLLECTION OBJECT AND NOT FLAGS ON THE ACTIVITY OBJECT. The defect this replaces was a
// single name, `hasTransactions`, that meant "the array is non-empty" to one branch and "the
// response named the collection" to another; the two got merged and a real system's measured zero
// started printing "—". A value that travels WITH the collection it describes cannot be attached
// to the wrong one by accident, and a second collection needs no second set of flags.
export const UNKNOWN_ACTIVITY_COUNT = '—'

export type CountConfidence = {
  /** The response named this collection at all. Nothing may be printed as a number without it. */
  known: boolean
  /** What we hold is a prefix of a longer list, so every count over it is "at least". */
  lowerBound: boolean
  /**
   * The windows (in days) of THIS counter in which a row arrived that could not be placed against
   * this participant. A window listed here would print an undercount and prints nothing instead; a
   * window NOT listed here is unaffected and keeps its number, because the row that raised the
   * doubt could not have joined its count anyway.
   */
  incompleteWindows: readonly number[]
}

export const COUNT_UNKNOWN: CountConfidence = { known: false, lowerBound: false, incompleteWindows: [] }
export const COUNT_MEASURED: CountConfidence = { known: true, lowerBound: false, incompleteWindows: [] }

/**
 * Read one collection's completeness out of a response's `included` / `truncated`.
 *
 * `truncated` is only meaningful for a collection `included` names: a cut we were never told we
 * received is not a cut we know about, it is silence.
 */
export function collectionConfidence(
  name: string,
  included: string[] | null | undefined,
  truncated: string[] | null | undefined,
  incompleteWindows: readonly number[] = [],
): CountConfidence {
  const isIncluded = (included || []).includes(name)
  // A collection we were never told about cannot have per-window doubts either: there is no count
  // to undercount. "We were not told" already says everything, and says it for every window.
  if (!isIncluded) return COUNT_UNKNOWN
  return {
    known: true,
    lowerBound: (truncated || []).includes(name),
    incompleteWindows,
  }
}

/** One "7 / 30 / 90" cell of the Activity card, printed at the confidence of its collection. */
export function activityCounts(
  counts: Record<number, number> | null | undefined,
  windows: number[] | null | undefined,
  confidence: CountConfidence | null | undefined,
): string {
  const ws = (windows || []).slice()
  if (!confidence || !confidence.known) {
    return ws.map(() => UNKNOWN_ACTIVITY_COUNT).join(' / ')
  }
  // Per window, because that is per cell. A doubtful row clouds the windows it could be in and
  // leaves the others reading the number they were counted to.
  const clouded = new Set(confidence.incompleteWindows || [])
  const prefix = confidence.lowerBound ? '≥' : ''
  return ws
    .map((w) => (clouded.has(w) ? UNKNOWN_ACTIVITY_COUNT : `${prefix}${(counts || {})[w] ?? 0}`))
    .join(' / ')
}

/**
 * The drawer's incident-ratio row, which is fed by the snapshot's `incidents` collection on every
 * branch - the per-participant metrics endpoint publishes no such ratio.
 *
 * The subtle case is a participant ABSENT from the map. On a complete list that absence is a
 * measured "no incidents" and prints 0.00; on a CUT list it is indistinguishable from a row the
 * server dropped, so it prints nothing. Before this, the row was `(ratio || 0).toFixed(2)` and
 * printed a hard 0.00 for every participant in real mode, where the collection is never requested.
 */
export function incidentRatioDisplay(
  ratio: number | null | undefined,
  confidence: CountConfidence | null | undefined,
): string {
  // Not a windowed figure: it is one number over the whole collection, so ANY unplaceable row in it
  // is enough to make the ratio a lower bound of unknown size.
  if (!confidence || !confidence.known || (confidence.incompleteWindows || []).length > 0) {
    return UNKNOWN_ACTIVITY_COUNT
  }
  if (!Number.isFinite(Number(ratio))) {
    return confidence.lowerBound ? UNKNOWN_ACTIVITY_COUNT : (0).toFixed(2)
  }
  const prefix = confidence.lowerBound ? '≥' : ''
  return `${prefix}${Number(ratio).toFixed(2)}`
}

/**
 * The notices printed under the Activity card, in the order the operator reads them.
 *
 * F-013-R5 (CROSS review of 013). These used to be a `v-if` / `v-else-if` / `v-else-if` chain,
 * written twice - once in GraphAnalyticsDrawer.vue, once in tabs/RiskTab.vue - i.e. as three
 * statements only one of which could ever be on screen. That held exactly as long as a doubt
 * blanked the WHOLE collection: while `incompleteWindows` emptied every cell, a suppressed
 * truncation notice explained nothing, because no "≥" could be showing at the same time.
 *
 * F-013-R4 made the doubt per window, and the premise died with it. A cut collection that also
 * carries one unplaceable row now prints "≥1 / ≥1 / —": two caveats, both true, one of them mute.
 * The operator was left reading a "≥" with nothing on the card saying what it meant.
 *
 * So the two caveats are now INDEPENDENT, and both are printed when both hold. What remains
 * ordered is only what is genuinely exclusive:
 *
 *   * "we were not told about this collection" cannot coexist with either of the other two, and
 *     not because this function says so: `collectionConfidence` returns COUNT_UNKNOWN for an
 *     uncarried collection, so `lowerBound` is false and `incompleteWindows` is empty by
 *     construction. The `else` below states that rather than establishing it.
 *   * `incidents` / `audit_log` fail independently of `transactions`, so their sentence is not part
 *     of the transactions group at all: it is appended, never substituted.
 *
 * WHAT IS DELIBERATELY *NOT* DONE HERE. The truncation notice is not narrowed to "a ≥ is actually
 * visible" - i.e. it still appears when the cut collection's every window happens to be clouded and
 * so every cell reads "—". Suppressing a true statement about the snapshot because the rendering
 * made it momentarily redundant is the very move this finding is about; a redundant sentence is
 * noise, a missing one is a number the operator cannot read correctly.
 *
 * WHY IT RETURNS THE KEYS INSTEAD OF THE CARDS RENDERING THEM. Two templates showed the same block
 * and nothing in the tree could be handed to a test - which is how a chain went wrong in both
 * copies at once and stayed green. One list, two `v-for`s, and the tests judge this function.
 */
export type ActivityNoticeKind =
  | 'transactionsNotIncluded'
  | 'transactionsUnattributable'
  | 'transactionsTruncated'
  | 'snapshotCollectionsNotIncluded'

export type ActivityNotice = {
  kind: ActivityNoticeKind
  /** el-alert severity. Only the truncation notice is informational: the count still stands. */
  type: 'warning' | 'info'
  titleKey: string
  descriptionKey: string
}

type ActivityConfidences = {
  payments: CountConfidence
  clearings: CountConfidence
  incidents: CountConfidence
  auditLog: CountConfidence
}

const NOTICES: Record<ActivityNoticeKind, ActivityNotice> = {
  transactionsNotIncluded: {
    kind: 'transactionsNotIncluded',
    type: 'warning',
    titleKey: 'graph.analytics.activity.transactionsNotIncludedTitle',
    descriptionKey: 'graph.analytics.activity.transactionsNotIncludedDescription',
  },
  transactionsUnattributable: {
    kind: 'transactionsUnattributable',
    type: 'warning',
    titleKey: 'graph.analytics.activity.transactionsUnattributableTitle',
    descriptionKey: 'graph.analytics.activity.transactionsUnattributableDescription',
  },
  transactionsTruncated: {
    kind: 'transactionsTruncated',
    type: 'info',
    titleKey: 'graph.analytics.activity.transactionsTruncatedTitle',
    descriptionKey: 'graph.analytics.activity.transactionsTruncatedDescription',
  },
  snapshotCollectionsNotIncluded: {
    kind: 'snapshotCollectionsNotIncluded',
    type: 'warning',
    titleKey: 'graph.analytics.activity.snapshotCollectionsNotIncludedTitle',
    descriptionKey: 'graph.analytics.activity.snapshotCollectionsNotIncludedDescription',
  },
}

export function activityNotices(activity: ActivityConfidences | null | undefined): ActivityNotice[] {
  if (!activity) return []
  const out: ActivityNotice[] = []

  if (!activity.payments.known) {
    // Nothing else can be true of a collection we were never told about - see above.
    out.push(NOTICES.transactionsNotIncluded)
  } else {
    // Both of these, whenever both hold. `payments` and `clearings` describe one collection, so
    // either answers "was it cut"; the doubt is per counter since F-013-R4, so it is asked of both.
    if (activity.payments.incompleteWindows.length || activity.clearings.incompleteWindows.length) {
      out.push(NOTICES.transactionsUnattributable)
    }
    if (activity.payments.lowerBound) out.push(NOTICES.transactionsTruncated)
  }

  if (!activity.incidents.known || !activity.auditLog.known) {
    out.push(NOTICES.snapshotCollectionsNotIncluded)
  }

  return out
}
