/**
 * The rules of the Config screen that do not need a screen (032 S7, D-17): what kind of control a value gets, which
 * section a key belongs to, which rows the operator changed, and the PATCH body made of them.
 */

export type RowKind = 'boolean' | 'number' | 'string' | 'json'
export type Row = { key: string; kind: RowKind; value: unknown }

// The sections of the keys the server lets an operator change (`mutable` in `GET /admin/config`; the client facade
// drops the rest). Keys that are read once at start (the log level, the integrity job) never reach the page, so they
// have no section; a mutable key the page does not know yet lands in `other` instead of disappearing.
export type SectionId = 'featureFlags' | 'rateLimit' | 'routing' | 'other'

export function kindOf(value: unknown): RowKind {
  if (typeof value === 'boolean') return 'boolean'
  if (typeof value === 'number') return 'number'
  if (typeof value === 'string') return 'string'
  return 'json'
}

/** The editable rows of a config object, sorted by key; a structured value is edited as its JSON text. */
export function toRows(obj: Record<string, unknown>): Row[] {
  return Object.entries(obj)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, value]) => {
      const kind = kindOf(value)
      if (kind === 'json') return { key, kind, value: JSON.stringify(value, null, 2) }
      return { key, kind, value }
    })
}

export function sectionForKey(key: string): SectionId {
  const k = String(key || '').trim().toUpperCase()
  if (k.startsWith('FEATURE_FLAGS_') || k === 'CLEARING_ENABLED') return 'featureFlags'
  if (k.startsWith('RATE_LIMIT_')) return 'rateLimit'
  if (k.startsWith('ROUTING_')) return 'routing'
  return 'other'
}

/** The i18n key of the unit a key's name implies (`..._SECONDS`, a count), or `null`. */
export function unitHintKey(key: string): string | null {
  const k = String(key || '').trim().toUpperCase()
  if (k.endsWith('_SECONDS')) return 'config.helpFallback.units.seconds'
  if (k.includes('_REQUESTS') || k.endsWith('_COUNT') || k.includes('_MAX_')) return 'config.helpFallback.units.count'
  return null
}

/** Keys of the rows whose value differs from what the server sent. */
export function dirtyKeysOf(rows: readonly Row[], original: Record<string, unknown>): string[] {
  const dirty: string[] = []
  for (const row of rows) {
    const originalValue = original[row.key]
    const comparableOriginal = row.kind === 'json' ? JSON.stringify(originalValue, null, 2) : originalValue
    if (comparableOriginal !== row.value) dirty.push(row.key)
  }
  return dirty
}

export type PatchResult = { patch: Record<string, unknown> } | { invalidJsonKey: string }

/** The PATCH body for the given dirty keys; a structured value whose text is not JSON refuses the whole patch. */
export function buildPatch(rows: readonly Row[], keys: readonly string[]): PatchResult {
  const rowByKey = new Map(rows.map((r) => [r.key, r] as const))
  const patch: Record<string, unknown> = {}
  for (const key of keys) {
    const row = rowByKey.get(key)
    if (!row) continue
    if (row.kind === 'json') {
      try {
        patch[key] = JSON.parse(String(row.value))
      } catch {
        return { invalidJsonKey: key }
      }
    } else {
      patch[key] = row.value
    }
  }
  return { patch }
}
