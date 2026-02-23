/**
 * Small string normalization helpers.
 *
 * Kept intentionally tiny: used across components/composables to avoid repeating
 * `String(x ?? '').toLowerCase()` and similar.
 */

export function toLower(v: unknown): string {
  return String(v ?? '').toLowerCase()
}

export function toLowerTrim(v: unknown): string {
  return String(v ?? '').trim().toLowerCase()
}
