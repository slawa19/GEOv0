/**
 * Minimal status helpers shared across UI composables.
 */

export function isActiveStatus(v: unknown): boolean {
  // Default to active when status is missing to keep backwards-compat.
  const st = String(v ?? 'active').trim().toLowerCase()
  return st === 'active'
}

