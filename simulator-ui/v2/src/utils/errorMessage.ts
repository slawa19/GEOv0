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
 * `text` with the server's correlation id of `e` appended as `(ref: <id>)`, when `e` carries one. For texts the
 * client composes itself (refusal texts) so that they can be found in the server log like any other error.
 * Not repeated when the text already shows the id.
 */
export function withRequestRef(text: string, e: unknown): string {
  const requestId = requestIdOf(e)
  if (!requestId || text.includes(requestId)) return text
  return text ? `${text} (ref: ${requestId})` : `(ref: ${requestId})`
}

/**
 * Extracts a human-readable error message from an unknown caught value.
 *
 * When the error carries the server's correlation id, the text ends with `(ref: <id>)`, so a message seen in the
 * UI can be found in the server log (AGENTS.md section 12).
 */
export function extractErrorMessage(e: unknown): string {
  return withRequestRef(baseMessage(e), e)
}
