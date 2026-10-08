import { isJwtLike } from '../utils/isJwtLike'

export type HttpConfig = {
  apiBase: string
  accessToken?: string | null
}

export function applyAuthHeaders(headers: Headers, token?: string | null): void {
  const t = String(token ?? '').trim()
  if (!t) return

  if (isJwtLike(t)) {
    headers.set('Authorization', `Bearer ${t}`)
    return
  }

  headers.set('X-Admin-Token', t)
}

export function authHeaders(token?: string | null): Record<string, string> {
  const t = String(token ?? '').trim()
  if (!t) return {}
  return isJwtLike(t) ? { Authorization: `Bearer ${t}` } : { 'X-Admin-Token': t }
}

/**
 * Bound of ONE request - from the call to the last byte of the body (034 F-034-15). Same semantics as the admin
 * client (`admin-ui/src/constants/timing.ts`, `admin-ui/src/api/realApi.ts` `fetchBounded`); the code is a copy,
 * not a shared package, by decision.
 *
 * Ordinary reads and the quick commands of a run answer from memory or one query, so the admin default fits.
 */
export const DEFAULT_REQUEST_TIMEOUT_MS = 30_000
/**
 * For the calls that do their work INSIDE the request: the Interact/demo actions of a run
 * (`/simulator/runs/{id}/actions/*`, `.../payment-targets`). The first of them on a real run seeds the whole
 * scenario into the database (`_ensure_run_seeded`, `app/api/v1/simulator.py`), and a payment or a clearing pass
 * is computed before the answer. The duration is NOT measured: the value is the longest bound of the admin client
 * (`LONG_REQUEST_TIMEOUT_MS`), chosen so that a slow-but-working command is not reported as failed while the
 * server goes on to commit it.
 */
export const LONG_REQUEST_TIMEOUT_MS = 120_000

/** `ApiError.code` of a request that did not finish in time. Status is 0: no HTTP answer completed. */
export const API_TIMEOUT_CODE = 'TIMEOUT'

export type HttpRequestInit = RequestInit & {
  /** Bound of the whole exchange - headers AND body. Default `DEFAULT_REQUEST_TIMEOUT_MS`. */
  timeoutMs?: number
}

export class ApiError extends Error {
  status: number
  bodyText?: string
  /** Machine code of failures that have no HTTP answer to read it from (`API_TIMEOUT_CODE`); otherwise undefined. */
  code?: string
  /** The server's correlation id (`X-Request-ID` header or `error.request_id` in the body); null when it gave none. */
  requestId: string | null

  constructor(message: string, opts: { status: number; bodyText?: string; code?: string; requestId?: string | null }) {
    super(message)
    this.name = 'ApiError'
    this.status = opts.status
    this.bodyText = opts.bodyText
    this.code = opts.code
    this.requestId = opts.requestId ? opts.requestId : null
  }
}

/** True for the `ApiError` raised by the request bound; a caller's own cancellation is an `AbortError`, never this. */
export function isTimeoutError(e: unknown): e is ApiError {
  return e instanceof ApiError && e.code === API_TIMEOUT_CODE
}

function joinUrl(base: string, path: string): string {
  const b = base.replace(/\\/g, '/').replace(/\/+$/, '')
  const p = path.startsWith('/') ? path : `/${path}`
  return `${b}${p}`
}

function nonEmptyString(v: unknown): string | null {
  return typeof v === 'string' && v.trim() ? v.trim() : null
}

/** The correlation id of a failed answer: body first (`error.request_id`, then `request_id`), then the header. */
function requestIdOf(res: Response, bodyText: string | undefined): string | null {
  let fromBody: string | null = null
  if (bodyText) {
    try {
      const parsed: unknown = JSON.parse(bodyText)
      if (parsed && typeof parsed === 'object') {
        const obj = parsed as Record<string, unknown>
        const err = obj.error && typeof obj.error === 'object' ? (obj.error as Record<string, unknown>) : undefined
        fromBody = nonEmptyString(err?.request_id) ?? nonEmptyString(obj.request_id)
      }
    } catch {
      fromBody = null
    }
  }
  return fromBody ?? nonEmptyString(res.headers?.get('X-Request-ID'))
}

type Guard = <V>(p: Promise<V>) => Promise<V>

function abortReasonOf(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException('Aborted', 'AbortError')
}

/**
 * One `fetch` bounded in time, covering the read of the body: a server that sends the headers and then stalls must
 * not hold the caller for ever. The bound is enforced twice - the request is aborted, and every wait is raced
 * against the bound itself - so a transport that ignores the abort signal cannot hang us either.
 *
 * The caller's own `signal` is kept: its abort rejects with the caller's reason (an `AbortError` by default), never
 * with the timeout `ApiError`. The timer and the listener are released on every exit.
 */
async function boundedRequest<T>(
  method: string,
  path: string,
  url: string,
  init: RequestInit,
  timeoutMs: number,
  handle: (res: Response, guard: Guard) => Promise<T>,
): Promise<T> {
  const callerSignal = init.signal ?? null
  if (callerSignal?.aborted) throw abortReasonOf(callerSignal)

  const controller = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  let onCallerAbort: (() => void) | undefined

  const bound = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => {
      reject(new ApiError(`${method} ${path} -> timeout after ${timeoutMs}ms`, { status: 0, code: API_TIMEOUT_CODE }))
      controller.abort()
    }, timeoutMs)
    if (callerSignal) {
      onCallerAbort = () => {
        reject(abortReasonOf(callerSignal))
        controller.abort(callerSignal.reason)
      }
      callerSignal.addEventListener('abort', onCallerAbort, { once: true })
    }
  })
  // Consumed by the races below; without this a rejection after they settled would be an unhandled one.
  bound.catch(() => undefined)

  const guard: Guard = (p) => Promise.race([p, bound])

  try {
    const res = await guard(fetch(url, { credentials: 'include', ...init, signal: controller.signal }))
    return await handle(res, guard)
  } finally {
    if (timer !== undefined) clearTimeout(timer)
    if (callerSignal && onCallerAbort) callerSignal.removeEventListener('abort', onCallerAbort)
  }
}

async function failFromResponse(res: Response, path: string, guard: Guard): Promise<never> {
  let bodyText: string | undefined
  try {
    bodyText = await guard(res.text())
  } catch (e) {
    // A bound or a cancel while the body is read is the failure itself, not "no body".
    if (e instanceof ApiError || (e as { name?: string } | null)?.name === 'AbortError') throw e
    bodyText = undefined
  }
  throw new ApiError(`HTTP ${res.status} ${res.statusText} for ${path}`, {
    status: res.status,
    bodyText,
    requestId: requestIdOf(res, bodyText),
  })
}

function splitInit(init?: HttpRequestInit): { fetchInit: RequestInit; timeoutMs: number } {
  const { timeoutMs, ...fetchInit } = init ?? {}
  const bound =
    typeof timeoutMs === 'number' && Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : DEFAULT_REQUEST_TIMEOUT_MS
  return { fetchInit, timeoutMs: bound }
}

export async function httpJson<T>(cfg: HttpConfig, path: string, init?: HttpRequestInit): Promise<T> {
  const url = joinUrl(cfg.apiBase, path)
  const { fetchInit, timeoutMs } = splitInit(init)

  const headers = new Headers(fetchInit.headers)
  headers.set('Accept', 'application/json')
  if (fetchInit.body != null) headers.set('Content-Type', 'application/json')
  applyAuthHeaders(headers, cfg.accessToken)

  // credentials: 'include' (set in boundedRequest) sends cookies (e.g. geo_sim_sid) with every request.
  // This enables anonymous-visitor cookie-auth when no accessToken is provided.
  const method = String(fetchInit.method ?? 'GET')
  return boundedRequest(method, path, url, { ...fetchInit, headers }, timeoutMs, async (res, guard) => {
    if (!res.ok) return failFromResponse(res, path, guard)
    if (res.status === 204) return undefined as T
    return (await guard(res.json())) as T
  })
}

export async function httpText(cfg: HttpConfig, path: string, init?: HttpRequestInit): Promise<string> {
  const url = joinUrl(cfg.apiBase, path)
  const { fetchInit, timeoutMs } = splitInit(init)

  const headers = new Headers(fetchInit.headers)
  const accept = fetchInit.headers ? new Headers(fetchInit.headers).get('Accept') : null
  headers.set('Accept', accept ? accept : '*/*')
  applyAuthHeaders(headers, cfg.accessToken)

  const method = String(fetchInit.method ?? 'GET')
  return boundedRequest(method, path, url, { ...fetchInit, headers }, timeoutMs, async (res, guard) => {
    if (!res.ok) return failFromResponse(res, path, guard)
    return await guard(res.text())
  })
}

export function httpUrl(cfg: HttpConfig, path: string): string {
  return joinUrl(cfg.apiBase, path)
}
