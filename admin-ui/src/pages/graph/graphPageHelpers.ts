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

/**
 * Денежная ячейка графа (F-012-7).
 *
 * Раньше здесь стояло `formatDecimalFixed(v, 2)`: два знака для любой величины, в том числе для
 * величин эквивалента с `precision: 1`.
 * Точность обязан назвать вызывающий — кодом эквивалента строки, а не местом вывода.
 */
export function money(
  value: string,
  equivalent: unknown,
  precisionByEq: ReadonlyMap<string, number>,
): string {
  return formatMoneyByEquivalent(value, equivalent, precisionByEq)
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
