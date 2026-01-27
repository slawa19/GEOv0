export function asFiniteNumber(v: unknown): number {
  if (typeof v === 'number') return Number.isFinite(v) ? v : 0
  if (typeof v === 'string') {
    const n = Number(v)
    return Number.isFinite(n) ? n : 0
  }
  return 0
}

export function formatAmount2(v: number): string {
  if (!Number.isFinite(v)) return '0.00'
  return v.toFixed(2)
}
