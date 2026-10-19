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
export function requestIdOf(e: unknown): string | null {
  if (e === null || typeof e !== 'object' || !('requestId' in e)) return null
  const id = (e as { requestId?: unknown }).requestId
  return typeof id === 'string' && id.trim() ? id.trim() : null
}

/**
 * 048 S3 (decision 3, «одна форма ошибки»): a failure as the viewer is shown it - the sentence of the interface (`text`)
 * and, APART from it, the server's correlation id (`ref`, AGENTS.md section 12): the id is a muted second line
 * («код запроса: <id>» / «request id: <id>») and `data-request-id`, never glued into the sentence. `detail` is the
 * failure's identifiers - the HTTP status and the code («HTTP 409 CONFLICT»), or a class name - for the block's `title`
 * (data, not prose: no language of its own). `log` is the full technical text (with the server's or the runtime's own
 * English message) for one console line only. Neither is drawn.
 */
export type ShownError = {
  readonly text: string
  readonly ref: string | null
  readonly detail: string | null
  readonly log?: string | null
}

/**
 * 048 S3: `text` and the server's correlation id of `e` - SEPARATELY (until S3 the id was glued into the text as
 * «(код запроса: …)»). `ref` is null when `e` carries no id, or when the text already shows it (a text composed by an
 * earlier step that kept the id).
 */
export function withRequestRef(text: string, e: unknown): { text: string; ref: string | null } {
  const ref = requestIdOf(e)
  return { text, ref: ref && !text.includes(ref) ? ref : null }
}

function identifiersOf(e: unknown): { status: number | null; code: string | null; name: string | null } {
  const o = (e !== null && typeof e === 'object' ? e : {}) as { status?: unknown; code?: unknown; name?: unknown }
  const status = typeof o.status === 'number' && o.status > 0 ? o.status : null
  const code = typeof o.code === 'string' && o.code.trim() && !/^HTTP_\d+$/.test(o.code.trim()) ? o.code.trim() : null
  const name = typeof o.name === 'string' && o.name.trim() && o.name !== 'Error' ? o.name.trim() : null
  return { status, code, name }
}

/**
 * 048 S3: the identifiers of a failure, for the `title` of the block - the HTTP status and the code of an answer
 * («HTTP 409 CONFLICT»), the class name of a value that is not one («TypeError»). No message: a title is said by the
 * screen too, and the server's or the runtime's English prose has no place on a screen of another language. Null when
 * there is nothing to name.
 */
export function errorDetail(e: unknown): string | null {
  const { status, code, name } = identifiersOf(e)
  const parts: string[] = []
  if (status !== null) parts.push(`HTTP ${status}`)
  if (code) parts.push(code)
  if (status === null && !code && name) parts.push(name)
  return parts.length ? parts.join(' ') : null
}

/**
 * 048 S3: the full technical text of a failure - for one console line only: the identifiers and the message as it came
 * (`HTTP 409 CONFLICT: Equivalent UAH is not active`, `TypeError: Failed to fetch`; an `ApiError`'s own message, which
 * already starts with its status, as it is). Null for a value with nothing to say.
 */
export function errorLog(e: unknown): string | null {
  if (e === null || e === undefined) return null
  if (typeof e === 'string') return e.trim() || null
  const message = baseMessage(e).trim()
  const { status, code } = identifiersOf(e)
  const lead = errorDetail(e) ?? ''
  if (!message || message === lead) return lead || null
  if (status !== null && !code && message.startsWith(`HTTP ${status}`)) return message
  return lead ? `${lead}: ${message}` : message
}

/** 048 S3: the shown form of a failure told by `text` (the caller's sentence): the id, the identifiers, the log - from `e`. */
export function shownError(text: string, e: unknown): ShownError {
  const { ref } = withRequestRef(text, e)
  const log = errorLog(e)
  // A log that only repeats the sentence (a text the client composed itself) adds nothing.
  return { text, ref, detail: errorDetail(e), log: log && log !== text ? log : null }
}

/**
 * The message of an error with the server's correlation id appended as `(ref: <id>)` - a DIAGNOSTIC text in English
 * (the console), never a text of the interface: since 048 S3 a viewer is shown a sentence of the dictionary and the id
 * on its own line (`ShownError`). Not repeated when the message already shows the id.
 */
export function extractErrorMessage(e: unknown): string {
  const text = baseMessage(e)
  const { ref } = withRequestRef(text, e)
  return ref ? (text ? `${text} (ref: ${ref})` : `(ref: ${ref})`) : text
}
