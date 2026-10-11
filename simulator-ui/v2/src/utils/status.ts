/**
 * Minimal status helpers shared across UI composables.
 */

import { toLower, toLowerTrim } from './stringHelpers'

export function isActiveStatus(v: unknown): boolean {
  // Default to active when status is missing to keep backwards-compat.
  const st = toLowerTrim(v ?? 'active')
  return st === 'active'
}

/**
 * A run in one of these states is live: it ticks or can be resumed, and its own snapshot is the scene. One rule
 * for the three places that ask (034 S8, F-034-D16). Not decided here: what an UNKNOWN status means - each caller
 * states that itself.
 */
export function isLiveRunState(state: unknown): boolean {
  const st = toLower(state)
  return st === 'running' || st === 'paused' || st === 'created' || st === 'stopping'
}
