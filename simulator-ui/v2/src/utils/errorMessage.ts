/**
 * Extracts a human-readable error message from an unknown caught value.
 */
export function extractErrorMessage(e: unknown): string {
  if (e instanceof Error) return e.message
  if (typeof e === 'string') return e
  if (e !== null && typeof e === 'object' && 'message' in e) {
    const message = (e as { message?: unknown }).message
    if (typeof message === 'string') return message
    if (message != null) return String(message)
  }
  return String(e)
}
