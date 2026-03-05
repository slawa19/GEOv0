/**
 * Very small heuristic: JWT is typically three base64url segments separated by dots.
 * Used to route auth to either Authorization: Bearer <jwt> or X-Admin-Token: <token>.
 */
export function isJwtLike(token: string): boolean {
  const t = String(token ?? '').trim()
  return t.split('.').length === 3
}
