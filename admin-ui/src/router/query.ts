import type { LocationQuery, LocationQueryRaw, LocationQueryValueRaw } from 'vue-router'

export function readQueryString(v: unknown): string {
  if (typeof v === 'string') return v
  if (Array.isArray(v)) return typeof v[0] === 'string' ? v[0] : ''
  return ''
}

export function normalizeLocationQueryValue(v: unknown): LocationQueryValueRaw | LocationQueryValueRaw[] | undefined {
  // Vue Router expects query values to be string | null (or arrays of those)
  if (v === undefined) return undefined
  if (v === null) return null

  if (Array.isArray(v)) {
    const arr: LocationQueryValueRaw[] = []
    for (const item of v) {
      if (item === undefined) continue
      if (item === null) arr.push(null)
      else if (typeof item === 'string') arr.push(item)
      else if (typeof item === 'number' || typeof item === 'boolean' || typeof item === 'bigint') arr.push(String(item))
      else arr.push(String(item))
    }
    return arr
  }

  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean' || typeof v === 'bigint') return String(v)
  return String(v)
}

export function toLocationQueryRaw(query: Record<string, unknown>): LocationQueryRaw {
  const out: LocationQueryRaw = {}
  for (const [key, value] of Object.entries(query)) {
    const normalized = normalizeLocationQueryValue(value)
    if (normalized === undefined) continue
    out[key] = normalized
  }
  return out
}

export function pickQueryStrings(
  query: LocationQuery | Record<string, unknown>,
  keys: string[],
): Record<string, string> {
  const out: Record<string, string> = {}
  for (const key of keys) {
    const v = (query as Record<string, unknown>)[key]
    const s = readQueryString(v).trim()
    if (s) out[key] = s
  }
  return out
}

export function carryScenarioQuery(query: LocationQuery | Record<string, unknown>): Record<string, string> {
  return pickQueryStrings(query, ['scenario'])
}
