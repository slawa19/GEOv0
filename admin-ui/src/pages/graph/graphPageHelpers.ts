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

// F-013-1 / T1302. How a transaction-derived counter is allowed to be printed.
//
// The Activity card used to print `0 / 0 / 0` whether the page had counted zero payments or had
// never asked the server for any. Two different facts, one glyph. This function keeps the three
// cases apart at the only place where they become pixels:
//
//   * the collection was never carried  -> "-" per window. We have no measurement; a zero here is
//     an assertion the client is not entitled to make.
//   * some rows could not be attributed -> "-" per window, for the same reason: the rows exist and
//     are missing from the count, so the number would be an undercount labelled as a total.
//   * the collection was truncated      -> the counts are lower bounds over a prefix of a longer
//     list, so each is printed with a leading "≥".
//   * otherwise                         -> the plain number, which is now a real measurement.
export const UNKNOWN_ACTIVITY_COUNT = '—'

export type ActivityCountConfidence = {
  hasTransactions: boolean
  transactionsTruncated: boolean
  transactionsAttributable: boolean
}

export function activityTransactionCounts(
  counts: Record<number, number> | null | undefined,
  windows: number[] | null | undefined,
  confidence: ActivityCountConfidence,
): string {
  const ws = (windows || []).slice()
  if (!confidence.hasTransactions || !confidence.transactionsAttributable) {
    return ws.map(() => UNKNOWN_ACTIVITY_COUNT).join(' / ')
  }
  const prefix = confidence.transactionsTruncated ? '≥' : ''
  return ws.map((w) => `${prefix}${(counts || {})[w] ?? 0}`).join(' / ')
}
