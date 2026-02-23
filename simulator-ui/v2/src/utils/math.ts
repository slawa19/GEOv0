export function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v))
}

export function clamp01(v: number) {
  return clamp(v, 0, 1)
}

/**
 * Quantize a value to the nearest multiple of `step`.
 *
 * Used for deterministic cache keys (e.g. gradients / glow sprites).
 * Must remain stable: non-finite values map to 0.
 */
export function quantize(v: number, step = 0.5) {
  if (!Number.isFinite(v)) return 0
  return Math.round(v / step) * step
}
