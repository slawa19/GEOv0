import { t } from '../i18n'
import { ApiException } from './apiException'

export type DescribedError = {
  /** What to show: the message, an actionable hint when there is one, and `(ref: <request id>)` when the server gave an id. */
  text: string
  /** The server's correlation id of the failed request, when there is one (`ApiException.requestId`). */
  requestId: string | null
}

function safeString(v: unknown): string {
  if (v === null || v === undefined) return ''
  try {
    return String(v)
  } catch {
    return ''
  }
}

function urlOf(e: ApiException): string {
  const details = e.details
  if (!details || typeof details !== 'object') return ''
  return safeString((details as Record<string, unknown>).url)
}

function hintOf(e: ApiException): string | null {
  if (e.status === 401 || e.status === 403) return t('error.hint.notAuthorized')
  if (e.status === 404) {
    const url = urlOf(e)
    // A relative `/api/...` URL went to whoever serves the UI (the Vite dev server), not to the backend.
    return url.includes('localhost:5173') || url.startsWith('/api/') ? t('error.hint.devServer') : t('error.hint.endpointNotFound')
  }
  return null
}

/**
 * The ONE place an error becomes text for the operator (032 S6, E-3 / D-11 / E-18): pages, composables and stores
 * call this instead of reading `e.message` themselves, so every failure is worded, localized and referenced alike.
 *
 * `fallbackKey` is the i18n key of the sentence to show when the error carries no message of its own.
 *
 * This function only describes; it raises no toast. Who shows the text (an inline alert, a toast, both) is the
 * caller's decision, and the transport (`requestJson`) never toasts - one failure, one message.
 */
export function describeError(e: unknown, fallbackKey?: string): DescribedError {
  const fallback = fallbackKey ? t(fallbackKey) : t('error.unknown')

  if (e instanceof ApiException) {
    const title = e.message || `HTTP ${e.status}`
    const hint = hintOf(e)
    const body = hint ? `${title} — ${hint}` : title
    const requestId = e.requestId
    return { text: requestId ? `${body} ${t('error.ref', { id: requestId })}` : body, requestId }
  }

  if (e instanceof Error) {
    const msg = (e.message || '').trim()
    // The browser's way of saying "no answer at all" (TypeError: Failed to fetch).
    if (msg.toLowerCase().includes('failed to fetch')) {
      return { text: `${t('error.network.title')} — ${t('error.network.hint')}`, requestId: null }
    }
    return { text: msg || fallback, requestId: null }
  }

  return { text: safeString(e).trim() || fallback, requestId: null }
}
