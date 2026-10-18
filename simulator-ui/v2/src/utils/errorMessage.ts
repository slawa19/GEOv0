import type { UiLocale } from '../i18n/locale'
import { t } from '../i18n/t'

/** The message an error carries, as it is - no request reference added (`extractErrorMessage` adds one). */
export function plainErrorMessage(e: unknown): string {
  return baseMessage(e)
}

/**
 * Codex §15 round 1: what identifies a failure, with no words of any language - the HTTP status (or the transport code
 * of an answer that did not come) and the server's request id, e.g. "HTTP 500 · req-1f2e". For a surface that tells the
 * failure in its own words and shows this beside them (the analytics panel); a value that is not an HTTP failure has
 * only its class name, never its message.
 */
export function errorReference(e: unknown): string {
  const o = (e !== null && typeof e === 'object' ? e : {}) as { status?: unknown; code?: unknown; name?: unknown }
  const parts: string[] = []
  if (typeof o.code === 'string' && o.code.trim()) parts.push(o.code.trim())
  else if (typeof o.status === 'number' && o.status > 0) parts.push(`HTTP ${o.status}`)
  else parts.push(typeof o.name === 'string' && o.name.trim() ? o.name.trim() : 'Error')
  const id = requestIdOf(e)
  if (id) parts.push(id)
  return parts.join(' · ')
}

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
 *
 * 046 wave 2: the reference is in the language of the TEXT it follows (`common.requestRef`), which only the caller
 * knows: a caller that composes its text in the language of the interface passes that language. Without one it is
 * English - the language of a server message (`extractErrorMessage`) and of the texts not yet moved into the
 * dictionary (waves 1 and 3) - so no English text gets a Russian tail.
 */
export function withRequestRef(text: string, e: unknown, locale: UiLocale = 'en'): string {
  const requestId = requestIdOf(e)
  if (!requestId || text.includes(requestId)) return text
  return text ? t('common.requestRef', { text, id: requestId }, locale) : t('common.requestRefOnly', { id: requestId }, locale)
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
