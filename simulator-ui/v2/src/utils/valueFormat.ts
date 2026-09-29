export function emptyToNull(v: unknown): string | number | null {
  if (v == null) return null
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  if (typeof v === 'string') {
    const s = v.trim()
    return s ? s : null
  }
  return null
}

export function emptyToNullString(v: unknown): string | null {
  if (v == null) return null
  const s = String(v).trim()
  return s ? s : null
}

export function renderOrDash(v: unknown): string {
  if (v == null) return '—'
  const s = String(v).trim()
  return s ? s : '—'
}

/** 026 `T2602`: `available` is signed; a negative one is the debt's excess over a lowered limit, not a payable amount. */
export function renderAvailable(v: unknown): string {
  const s = renderOrDash(v)
  return s.startsWith('-') ? `over limit by ${s.slice(1)}` : s
}
