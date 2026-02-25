export function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v))
}

/**
 * Return `v` if it is a finite number, otherwise return `def`.
 * Used to sanitize NaN/Infinity inputs before they reach rendering/geometry code.
 */
export function finiteOr(v: number, def: number): number {
  return Number.isFinite(v) ? v : def
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

/**
 * Safe viewport clamp: prevents inverted range when `size < 2 * margin`.
 * The effective margin is shrunk to `size / 2 - 1` (never negative),
 * so the range [m, size - m] is always valid.
 */
export function safeClampToViewport(value: number, margin: number, size: number): number {
  const m = Math.min(margin, Math.max(0, size / 2 - 1))
  return clamp(value, m, size - m)
}
