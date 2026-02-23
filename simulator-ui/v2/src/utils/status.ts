/**
 * Minimal status helpers shared across UI composables.
 */

import { toLowerTrim } from './stringHelpers'

export function isActiveStatus(v: unknown): boolean {
  // Default to active when status is missing to keep backwards-compat.
  const st = toLowerTrim(v ?? 'active')
  return st === 'active'
}

