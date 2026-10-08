function baseMessage(e: unknown): string {
  if (e instanceof Error) return e.message
  if (typeof e === 'string') return e
  if (e !== null && typeof e === 'object' && 'message' in e) {
    const message = (e as { message?: unknown }).message
    if (typeof message === 'string') return message
    if (message != null) return String(message)
  }
  return String(e)
}

/** The server's correlation id carried by an error (`ApiError.requestId`), or null. Duck-typed: no import cycle. */
function requestIdOf(e: unknown): string | null {
  if (e === null || typeof e !== 'object' || !('requestId' in e)) return null
  const id = (e as { requestId?: unknown }).requestId
  return typeof id === 'string' && id.trim() ? id.trim() : null
}

/**
 * Extracts a human-readable error message from an unknown caught value.
 *
 * When the error carries the server's correlation id, the text ends with `(ref: <id>)`, so a message seen in the
 * UI can be found in the server log (AGENTS.md section 12).
 */
export function extractErrorMessage(e: unknown): string {
  const message = baseMessage(e)
  const requestId = requestIdOf(e)
  if (!requestId || message.includes(requestId)) return message
  return message ? `${message} (ref: ${requestId})` : `(ref: ${requestId})`
}
