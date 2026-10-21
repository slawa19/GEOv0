import { API_TIMEOUT_CODE, ApiError, DEFAULT_REQUEST_TIMEOUT_MS, isTimeoutError } from './http'

export type SseParsedMessage = {
  id?: string
  event?: string
  data?: string
}

export type SseParserState = {
  buffer: string
}

export function parseSseChunk(state: SseParserState, chunk: string): { messages: SseParsedMessage[] } {
  // Normalize CRLF → LF to keep parsing predictable.
  state.buffer += chunk.replace(/\r\n/g, '\n')

  const messages: SseParsedMessage[] = []

  while (true) {
    const sep = state.buffer.indexOf('\n\n')
    if (sep < 0) break

    const rawFrame = state.buffer.slice(0, sep)
    state.buffer = state.buffer.slice(sep + 2)

    if (!rawFrame.trim()) continue

    let id: string | undefined
    let event: string | undefined
    const dataLines: string[] = []

    for (const line of rawFrame.split('\n')) {
      if (!line) continue
      if (line.startsWith(':')) continue // comment / keep-alive

      if (line.startsWith('id:')) {
        id = line.slice(3).trim()
        continue
      }

      if (line.startsWith('event:')) {
        event = line.slice(6).trim()
        continue
      }

      if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart())
        continue
      }

      // Ignore unknown fields.
    }

    const data = dataLines.length ? dataLines.join('\n') : undefined
    if (id === undefined && event === undefined && data === undefined) continue
    messages.push({ id, event, data })
  }

  return { messages }
}

export type SseConnectOpts = {
  url: string
  headers?: Record<string, string>
  lastEventId?: string | null
  signal?: AbortSignal
  onMessage: (msg: SseParsedMessage) => void
}

/**
 * The server's keep-alive interval: a run's stream (`app/api/v1/simulator.py`, `_run_events_stream`,
 * `keepalive_sec = 15`) and the idle stream of `/simulator/events` both write a `: keep-alive` comment after 15 s
 * without an event, whatever the run's state (paused or slow included). A mirror, not a contract: change both together.
 */
export const SSE_SERVER_KEEPALIVE_MS = 15_000
/**
 * Bound of the silence between two reads of an OPEN stream (BACKLOG 034-7). Any byte resets it - a keep-alive comment
 * as much as a frame - so a healthy stream is never silent longer than one keep-alive interval. Three intervals: one
 * lost or late keep-alive and proxy/scheduler jitter do not trip it; a stream silent for three is not delivering, and
 * without the bound a hang without EOF kept the stream `open` (a live network on screen) for ever. Expiry is a failure
 * of the connection like a network error: the caller reconnects through its back-off with its replay cursor.
 */
export const SSE_IDLE_TIMEOUT_MS = 3 * SSE_SERVER_KEEPALIVE_MS
/**
 * Bound from the call to the first byte of the stream - or, for a refused answer, to the end of its body. The REST
 * client's bound of one request (`DEFAULT_REQUEST_TIMEOUT_MS`): the server answers a run's stream with its bootstrap
 * frame at once, and the idle stream with a keep-alive at once.
 */
export const SSE_CONNECT_TIMEOUT_MS = DEFAULT_REQUEST_TIMEOUT_MS

function abortReasonOf(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException('Aborted', 'AbortError')
}

/**
 * One SSE connection, read until EOF. Bounded in time (BACKLOG 034-7): `SSE_CONNECT_TIMEOUT_MS` until the first byte
 * (or the end of a refused answer's body), then `SSE_IDLE_TIMEOUT_MS` between reads. An expired bound rejects with an
 * `ApiError` with code `API_TIMEOUT_CODE` and status 0 - the code the REST client gives its own timeout, classified by
 * the same rules (`utils/runErrorClassification.ts`). As in `api/http.ts` (`boundedRequest`), the bound is enforced
 * twice: the request is aborted and every wait is raced against the bound, so a transport that ignores the abort cannot
 * hang the caller either; the reader is cancelled on every exit.
 *
 * The caller's own `signal` is kept: its abort rejects with the caller's reason (an `AbortError` by default), never with
 * the timeout. The timer and the listener are released on every exit.
 */
export async function connectSse(opts: SseConnectOpts): Promise<void> {
  const headers = new Headers(opts.headers)
  headers.set('Accept', 'text/event-stream')
  if (opts.lastEventId) headers.set('Last-Event-ID', opts.lastEventId)

  const callerSignal = opts.signal ?? null
  if (callerSignal?.aborted) throw abortReasonOf(callerSignal)

  const controller = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  let rejectBound!: (reason: unknown) => void
  const bound = new Promise<never>((_resolve, reject) => {
    rejectBound = reject
  })
  // Consumed by the races below; without this a rejection after they settled would be an unhandled one.
  bound.catch(() => undefined)
  const guard = <V>(p: Promise<V>): Promise<V> => Promise.race([p, bound])

  // (Re)starts the one timer of this connection: the connect bound first, then the idle bound after every byte.
  const arm = (ms: number, what: 'connect' | 'idle') => {
    if (timer !== undefined) clearTimeout(timer)
    timer = setTimeout(() => {
      timer = undefined
      const message =
        what === 'connect' ? `SSE timeout after ${ms}ms: no answer` : `SSE timeout after ${ms}ms without a byte from the stream`
      rejectBound(new ApiError(message, { status: 0, code: API_TIMEOUT_CODE, timeoutMs: ms }))
      controller.abort()
    }, ms)
  }
  const onCallerAbort = () => {
    rejectBound(abortReasonOf(callerSignal!))
    controller.abort(callerSignal!.reason)
  }
  callerSignal?.addEventListener('abort', onCallerAbort, { once: true })

  let reader: ReadableStreamDefaultReader<Uint8Array> | null = null
  try {
    arm(SSE_CONNECT_TIMEOUT_MS, 'connect')
    // credentials: 'include' sends cookies (e.g. geo_sim_sid) with SSE requests.
    // Required for anonymous-visitor cookie-auth (no Authorization header available for SSE).
    const res = await guard(fetch(opts.url, { headers, signal: controller.signal, credentials: 'include' }))
    if (!res.ok) {
      let bodyText = ''
      try {
        bodyText = await guard(res.text())
      } catch (e) {
        // A bound or a cancel while the body is read is the failure itself, not "no body"; a dropped connection is.
        if (callerSignal?.aborted) throw abortReasonOf(callerSignal)
        if (isTimeoutError(e)) throw e
        bodyText = ''
      }
      // The status travels on the error (034 S8, F-034-D14): the message carries the answer's body, so a caller
      // that looked for "404" or "410" in it would be reading the server's prose.
      throw new ApiError(`SSE HTTP ${res.status} ${res.statusText}: ${bodyText}`, { status: res.status, bodyText })
    }

    if (!res.body) throw new Error('SSE response has no body')

    reader = res.body.getReader()
    const decoder = new TextDecoder('utf-8')
    const state: SseParserState = { buffer: '' }

    while (true) {
      const { value, done } = await guard(reader.read())
      if (done) break
      if (!value) continue
      // Any byte proves the stream alive - a keep-alive comment too, which yields no message below.
      arm(SSE_IDLE_TIMEOUT_MS, 'idle')

      const chunk = decoder.decode(value, { stream: true })
      const { messages } = parseSseChunk(state, chunk)
      for (const m of messages) {
        try {
          opts.onMessage(m)
        } catch (err) {
          // Prevent a single bad event from killing the SSE read loop.
          // The caller can handle errors in onMessage if needed.
          console.error('[SSE] onMessage error:', err, 'event:', m.event, 'id:', m.id)
        }
      }
    }
  } finally {
    if (timer !== undefined) clearTimeout(timer)
    timer = undefined
    callerSignal?.removeEventListener('abort', onCallerAbort)
    if (reader) {
      try {
        void reader.cancel().catch(() => undefined)
      } catch {
        // An errored or released stream has nothing to cancel.
      }
    }
  }
}
