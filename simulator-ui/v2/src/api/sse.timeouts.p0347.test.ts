/**
 * BACKLOG 034-7 - reproducer for the time bounds of the SSE transport (`api/sse.ts`, `connectSse`).
 *
 * Target behaviour, RED on the client before the fix:
 *   (a) a stream that delivers a frame and then goes silent (no byte, no EOF) ends with an `ApiError` by timeout -
 *       the caller's existing failure path (reconnect with back-off) then runs; before, the read waited for ever;
 *   (b) a connection that never answers ends the same way;
 *   (c) a refused answer whose body never ends ends the same way, not "pending for ever";
 *   counter-checks: a healthy stream that sends only the server's keep-alive comments, at the server's interval
 *   (15 s, `app/api/v1/simulator.py` `keepalive_sec`), never trips, however long it lives; the caller's own
 *   cancellation stays an abort, not a timeout; no timer is left behind on any exit.
 *
 * Only controlled clocks (`vi.useFakeTimers`); each case asserts the clock was actually moved past the bound (a test
 * that never advanced the clock would prove nothing). The stubbed `fetch` ignores the abort signal for the BODY (a
 * `Response` over a hand-held stream is not tied to it), so the bound must hold even against a transport that does not
 * honour the abort - as the REST client's does (`api/http.ts`, `boundedRequest`).
 *
 * The bounds are not imported: any idle bound in (15 s, 60 s] and any connect bound up to 120 s satisfies the test.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './http'
import { connectSse, type SseParsedMessage } from './sse'

/** The server's keep-alive interval (`app/api/v1/simulator.py`, `_run_events_stream`, `keepalive_sec = 15`). */
const SERVER_KEEPALIVE_MS = 15_000
/** The largest idle bound this test accepts. */
const IDLE_UPPER_MS = 60_000
/** The largest connect bound this test accepts (the admin client's longest bound). */
const CONNECT_UPPER_MS = 120_000

type Outcome = { settled: false } | { settled: true; value: unknown } | { settled: true; error: unknown }

function track(call: Promise<unknown>): { outcome: () => Outcome } {
  let outcome: Outcome = { settled: false }
  call.then(
    (value) => {
      outcome = { settled: true, value }
    },
    (error: unknown) => {
      outcome = { settled: true, error }
    },
  )
  return { outcome: () => outcome }
}

/** A 200 `text/event-stream` answer whose bytes the test pushes by hand; `cancelled` records a released reader. */
function controllableStream() {
  let ctl!: ReadableStreamDefaultController<Uint8Array>
  const enc = new TextEncoder()
  const state = { cancelled: false }
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      ctl = c
    },
    cancel() {
      state.cancelled = true
    },
  })
  return {
    response: new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }),
    push: (text: string) => ctl.enqueue(enc.encode(text)),
    end: () => ctl.close(),
    state,
  }
}

/** A `fetch` that never answers by itself but, like a real one, rejects when its signal is aborted. */
function hangingFetch() {
  return vi.fn((_url: unknown, init?: RequestInit) => {
    return new Promise<Response>((_resolve, reject) => {
      const signal = init?.signal
      if (!signal) return
      const onAbort = () => reject(signal.reason ?? new DOMException('Aborted', 'AbortError'))
      if (signal.aborted) onAbort()
      else signal.addEventListener('abort', onAbort, { once: true })
    })
  })
}

const BOOTSTRAP = 'id: evt_1\nevent: simulator.event\ndata: {"type":"run_status","state":"running"}\n\n'

function expectTimeout(o: Outcome, message: string): void {
  expect(o.settled, message).toBe(true)
  const error = 'error' in o ? o.error : undefined
  expect(error, 'the transport ended without an error').toBeInstanceOf(ApiError)
  expect((error as ApiError).code).toBe('TIMEOUT')
  expect((error as ApiError).status).toBe(0)
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('034-7 (a): a stream that goes silent after a frame', () => {
  it('ends with ApiError TIMEOUT within the idle bound, and the reader is released', async () => {
    const s = controllableStream()
    vi.stubGlobal('fetch', vi.fn(async () => s.response))
    const seen: SseParsedMessage[] = []
    const call = track(connectSse({ url: 'http://sim.test/events', onMessage: (m) => seen.push(m) }))

    s.push(BOOTSTRAP)
    await vi.advanceTimersByTimeAsync(0)
    expect(seen.map((m) => m.id), 'the first frame was not delivered: the test drives nothing').toEqual(['evt_1'])
    expect(call.outcome().settled, 'the stream ended on its own before any wait').toBe(false)

    const before = Date.now()
    await vi.advanceTimersByTimeAsync(IDLE_UPPER_MS)
    expect(Date.now() - before, 'the fake clock did not move').toBe(IDLE_UPPER_MS)

    expectTimeout(call.outcome(), 'connectSse still waits for the next read after the idle bound: no idle bound exists')
    expect(s.state.cancelled, 'the silent stream was left open: its reader was not cancelled').toBe(true)
    expect(vi.getTimerCount(), 'a timer was left behind after the timeout').toBe(0)
  })
})

describe('034-7 (b): a connection that never answers', () => {
  it('ends with ApiError TIMEOUT within the connect bound', async () => {
    vi.stubGlobal('fetch', hangingFetch())
    const call = track(connectSse({ url: 'http://sim.test/events', onMessage: () => undefined }))

    await vi.advanceTimersByTimeAsync(CONNECT_UPPER_MS)

    expectTimeout(call.outcome(), 'connectSse still waits for the answer after the connect bound: no connect bound exists')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('an answer whose headers came but whose stream never sends a byte ends the same way', async () => {
    const s = controllableStream()
    vi.stubGlobal('fetch', vi.fn(async () => s.response))
    const call = track(connectSse({ url: 'http://sim.test/events', onMessage: () => undefined }))

    await vi.advanceTimersByTimeAsync(CONNECT_UPPER_MS)

    expectTimeout(call.outcome(), 'connectSse still waits for the first byte after the connect bound')
  })
})

describe('034-7 (c): a refused answer whose body never ends', () => {
  it('ends with ApiError TIMEOUT, not "pending for ever"', async () => {
    const body = new ReadableStream<Uint8Array>({ start() {} })
    vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status: 503, statusText: 'Service Unavailable' })))
    const call = track(connectSse({ url: 'http://sim.test/events', onMessage: () => undefined }))

    await vi.advanceTimersByTimeAsync(CONNECT_UPPER_MS)

    expectTimeout(call.outcome(), 'connectSse still reads the body of the refused answer after the bound')
    expect(vi.getTimerCount()).toBe(0)
  })
})

describe('034-7 counter-checks', () => {
  it('a healthy stream that sends only keep-alives at the server interval never trips (10 minutes)', async () => {
    const s = controllableStream()
    vi.stubGlobal('fetch', vi.fn(async () => s.response))
    const seen: SseParsedMessage[] = []
    const call = track(connectSse({ url: 'http://sim.test/events', onMessage: (m) => seen.push(m) }))

    s.push(BOOTSTRAP)
    await vi.advanceTimersByTimeAsync(0)
    for (let i = 0; i < 40; i += 1) {
      await vi.advanceTimersByTimeAsync(SERVER_KEEPALIVE_MS)
      // A keep-alive is a comment, not a message: the bound must count bytes, not parsed frames.
      s.push(': keep-alive\n\n')
      await vi.advanceTimersByTimeAsync(0)
    }
    expect(call.outcome().settled, 'a healthy stream (keep-alive every 15 s) was ended by the transport').toBe(false)
    expect(seen.map((m) => m.id), 'keep-alives were delivered as messages').toEqual(['evt_1'])

    // A later frame still arrives, and the normal EOF still ends the stream without error.
    s.push('id: evt_2\ndata: {}\n\n')
    s.end()
    await vi.advanceTimersByTimeAsync(0)
    const o = call.outcome()
    expect(o.settled && 'value' in o, 'the stream did not end normally on EOF').toBe(true)
    expect(seen.map((m) => m.id)).toEqual(['evt_1', 'evt_2'])
    expect(vi.getTimerCount(), 'a timer was left behind after EOF').toBe(0)
  })

  it("the caller's cancellation is an abort, not a timeout, and leaves no timer", async () => {
    const s = controllableStream()
    vi.stubGlobal('fetch', vi.fn(async () => s.response))
    const ctrl = new AbortController()
    const call = track(connectSse({ url: 'http://sim.test/events', signal: ctrl.signal, onMessage: () => undefined }))

    s.push(BOOTSTRAP)
    await vi.advanceTimersByTimeAsync(SERVER_KEEPALIVE_MS)
    ctrl.abort()
    await vi.advanceTimersByTimeAsync(0)

    const o = call.outcome()
    expect(o.settled, 'the caller aborted, the transport kept reading').toBe(true)
    const error = 'error' in o ? o.error : undefined
    expect(error).not.toBeInstanceOf(ApiError)
    expect((error as { name?: unknown }).name).toBe('AbortError')
    expect(vi.getTimerCount(), 'a timer was left behind after the caller aborted').toBe(0)
  })
})
