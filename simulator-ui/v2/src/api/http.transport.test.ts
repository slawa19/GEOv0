/**
 * 034 F-034-15 — reproducer for the HTTP transport of Simulator UI v2 (`api/http.ts`).
 *
 * Target behavior, RED on the current client (written before the fix, 034 T3401):
 *   1. a `fetch` that never answers ends with an `ApiError` by timeout (the whole exchange is bounded);
 *   2. a body read that never ends does the same (success path AND error path);
 *   3. the caller's own cancellation is kept and stays distinguishable from the timeout;
 *   4. the server's correlation id (`X-Request-ID` header or `error.request_id` in the body) reaches
 *      `ApiError.requestId`, and `extractErrorMessage` shows it as `(ref: <id>)` (§12 AGENTS.md).
 *
 * Only controlled clocks (`vi.useFakeTimers`): nothing here waits in real time. The bound used below is the
 * longest the admin client allows (`admin-ui/src/constants/timing.ts`, `LONG_REQUEST_TIMEOUT_MS`), so any
 * default up to that value satisfies the test; the value itself is the implementer's choice.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, DEFAULT_REQUEST_TIMEOUT_MS, httpJson, httpText, isTimeoutError, LONG_REQUEST_TIMEOUT_MS } from './http'
import { getRun, actionPaymentReal } from './simulatorApi'
import { extractErrorMessage } from '../utils/errorMessage'

const CFG = { apiBase: '/api/v1' }
const TIMEOUT_BOUND_MS = 120_000

type Outcome = { settled: false } | { settled: true; value: unknown } | { settled: true; error: unknown }

/** Starts the call and records how it ended, so "still pending" is observable without real waiting. */
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

/** A response whose headers have arrived but whose body never produces data. */
function responseWithHangingBody(status: number): Response {
  return new Response(new ReadableStream({ start() {} }), { status, headers: { 'Content-Type': 'application/json' } })
}

function expectTimeoutApiError(error: unknown): void {
  expect(error).toBeInstanceOf(ApiError)
  expect((error as ApiError).message).toMatch(/time(d)?[\s-]?out/i)
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('F-034-15: httpJson is bounded in time', () => {
  it('a fetch that never answers ends with ApiError by timeout', async () => {
    vi.stubGlobal('fetch', hangingFetch())
    const call = track(httpJson(CFG, '/simulator/runs'))

    await vi.advanceTimersByTimeAsync(TIMEOUT_BOUND_MS)

    const o = call.outcome()
    expect(o.settled, 'httpJson is still pending after the whole timeout bound: no timeout exists').toBe(true)
    expectTimeoutApiError('error' in o ? o.error : undefined)
  })

  it('a fetch that ignores its signal and never answers still ends with ApiError by timeout', async () => {
    // The bound must not depend on the transport honoring `signal`: the call itself has to give up.
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})))
    const call = track(httpJson(CFG, '/simulator/runs'))

    await vi.advanceTimersByTimeAsync(TIMEOUT_BOUND_MS)

    const o = call.outcome()
    expect(o.settled, 'httpJson is still pending after the whole timeout bound: no timeout exists').toBe(true)
    expectTimeoutApiError('error' in o ? o.error : undefined)
  })

  it('a success body that never ends ends with ApiError by timeout', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => responseWithHangingBody(200)))
    const call = track(httpJson(CFG, '/simulator/runs'))

    await vi.advanceTimersByTimeAsync(TIMEOUT_BOUND_MS)

    const o = call.outcome()
    expect(o.settled, 'httpJson is still pending while reading the body: the read is not bounded').toBe(true)
    expectTimeoutApiError('error' in o ? o.error : undefined)
  })

  it('an error-status body that never ends ends with ApiError by timeout', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => responseWithHangingBody(500)))
    const call = track(httpJson(CFG, '/simulator/runs'))

    await vi.advanceTimersByTimeAsync(TIMEOUT_BOUND_MS)

    const o = call.outcome()
    expect(o.settled, 'httpJson is still pending while reading an error body: the read is not bounded').toBe(true)
    expectTimeoutApiError('error' in o ? o.error : undefined)
  })

  it('counter-check: a prompt answer is not turned into a timeout', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ ok: 1 }), { status: 200, headers: { 'Content-Type': 'application/json' } })),
    )
    const call = httpJson<{ ok: number }>(CFG, '/simulator/runs')

    await vi.advanceTimersByTimeAsync(0)

    await expect(call).resolves.toEqual({ ok: 1 })
  })
})

describe('F-034-15: the caller\'s own cancellation is kept and distinguishable from the timeout', () => {
  it('aborting the caller signal rejects with the abort, not with a timeout ApiError', async () => {
    vi.stubGlobal('fetch', hangingFetch())
    const external = new AbortController()
    const call = track(httpJson(CFG, '/simulator/runs', { signal: external.signal }))

    external.abort()
    await vi.advanceTimersByTimeAsync(0)

    const o = call.outcome()
    expect(o.settled, 'the caller aborted but httpJson is still pending: the external signal is lost').toBe(true)
    const error = 'error' in o ? o.error : undefined
    expect((error as { name?: string } | undefined)?.name).toBe('AbortError')
    expect(error).not.toBeInstanceOf(ApiError)
  })

  it('an already aborted caller signal rejects at once with the abort', async () => {
    vi.stubGlobal('fetch', hangingFetch())
    const external = new AbortController()
    external.abort()
    const call = track(httpJson(CFG, '/simulator/runs', { signal: external.signal }))

    await vi.advanceTimersByTimeAsync(0)

    const o = call.outcome()
    expect(o.settled).toBe(true)
    const error = 'error' in o ? o.error : undefined
    expect((error as { name?: string } | undefined)?.name).toBe('AbortError')
    expect(error).not.toBeInstanceOf(ApiError)
  })

  it('counter-check: passing a caller signal that is never aborted does not remove the timeout', async () => {
    // Catches an implementation that "keeps the caller's signal" by replacing its own timeout controller.
    vi.stubGlobal('fetch', hangingFetch())
    const external = new AbortController()
    const call = track(httpJson(CFG, '/simulator/runs', { signal: external.signal }))

    await vi.advanceTimersByTimeAsync(TIMEOUT_BOUND_MS)

    const o = call.outcome()
    expect(o.settled, 'a call with a caller signal never times out').toBe(true)
    expectTimeoutApiError('error' in o ? o.error : undefined)
  })
})

describe('F-034-15: the correlation id reaches ApiError and the message', () => {
  type WithRequestId = ApiError & { requestId?: string | null }

  async function failOnce(response: Response): Promise<WithRequestId> {
    vi.stubGlobal('fetch', vi.fn(async () => response))
    const call = httpJson(CFG, '/simulator/runs')
    // Attach the handler before any timer step so a rejection is never reported as unhandled.
    const caught = call.then(
      () => {
        throw new Error('expected httpJson to reject')
      },
      (e: unknown) => e as WithRequestId,
    )
    await vi.advanceTimersByTimeAsync(0)
    return caught
  }

  it('X-Request-ID response header becomes ApiError.requestId and "(ref: ...)" in the message', async () => {
    const error = await failOnce(
      new Response(JSON.stringify({ error: { code: 'INTERNAL', message: 'boom' } }), {
        status: 500,
        headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'req-header-1' },
      }),
    )

    expect(error).toBeInstanceOf(ApiError)
    expect.soft(error.requestId).toBe('req-header-1')
    expect(extractErrorMessage(error)).toContain('(ref: req-header-1)')
  })

  it('error.request_id in the body becomes ApiError.requestId and "(ref: ...)" in the message', async () => {
    const error = await failOnce(
      new Response(JSON.stringify({ error: { code: 'INTERNAL', message: 'boom', request_id: 'req-body-1' } }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    expect(error).toBeInstanceOf(ApiError)
    expect.soft(error.requestId).toBe('req-body-1')
    expect(extractErrorMessage(error)).toContain('(ref: req-body-1)')
  })

  it('counter-check: an error without any id carries no requestId and no "(ref:" text', async () => {
    const error = await failOnce(
      new Response(JSON.stringify({ error: { code: 'INTERNAL', message: 'boom' } }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    expect(error).toBeInstanceOf(ApiError)
    expect(error.requestId ?? null).toBeNull()
    expect(extractErrorMessage(error)).not.toContain('(ref:')
  })
})

describe('F-034-15: the bound is per call, covers httpText, and leaves no timer behind', () => {
  const OK = () => new Response(JSON.stringify({ ok: 1 }), { status: 200, headers: { 'Content-Type': 'application/json' } })

  it('a per-call timeoutMs is honored: pending just before it, TIMEOUT ApiError at it', async () => {
    vi.stubGlobal('fetch', hangingFetch())
    const call = track(httpJson(CFG, '/x', { timeoutMs: 1_000 }))

    await vi.advanceTimersByTimeAsync(999)
    expect(call.outcome().settled).toBe(false)
    await vi.advanceTimersByTimeAsync(1)

    const o = call.outcome()
    expect(o.settled).toBe(true)
    const error = 'error' in o ? o.error : undefined
    expect(isTimeoutError(error)).toBe(true)
    expect((error as ApiError).status).toBe(0)
  })

  it('httpText is bounded the same way', async () => {
    vi.stubGlobal('fetch', hangingFetch())
    const call = track(httpText(CFG, '/x'))

    await vi.advanceTimersByTimeAsync(TIMEOUT_BOUND_MS)

    const o = call.outcome()
    expect(o.settled).toBe(true)
    expect(isTimeoutError('error' in o ? o.error : undefined)).toBe(true)
  })

  it('the default bound is DEFAULT_REQUEST_TIMEOUT_MS: pending just before it', async () => {
    vi.stubGlobal('fetch', hangingFetch())
    const call = track(httpJson(CFG, '/x'))

    await vi.advanceTimersByTimeAsync(DEFAULT_REQUEST_TIMEOUT_MS - 1)
    expect(call.outcome().settled).toBe(false)
    await vi.advanceTimersByTimeAsync(1)
    expect(call.outcome().settled).toBe(true)
  })

  it('an Interact action gets the long bound, an ordinary read the default one', async () => {
    vi.stubGlobal('fetch', hangingFetch())
    const read = track(getRun(CFG, 'run-1'))
    const action = track(actionPaymentReal(CFG, 'run-1', { from_pid: 'a', to_pid: 'b', amount: '1', equivalent: 'UAH' } as never))

    await vi.advanceTimersByTimeAsync(DEFAULT_REQUEST_TIMEOUT_MS)
    expect(isTimeoutError('error' in read.outcome() ? (read.outcome() as { error: unknown }).error : undefined)).toBe(true)
    expect(action.outcome().settled, 'the action was cut at the default bound').toBe(false)

    await vi.advanceTimersByTimeAsync(LONG_REQUEST_TIMEOUT_MS - DEFAULT_REQUEST_TIMEOUT_MS)
    expect(isTimeoutError('error' in action.outcome() ? (action.outcome() as { error: unknown }).error : undefined)).toBe(true)
  })

  it('no timer is left after success, an error answer, a timeout and a caller abort', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => OK()))
    await httpJson(CFG, '/x')
    expect(vi.getTimerCount(), 'success').toBe(0)

    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 500 })))
    await expect(httpJson(CFG, '/x')).rejects.toBeInstanceOf(ApiError)
    expect(vi.getTimerCount(), 'error answer').toBe(0)

    vi.stubGlobal('fetch', hangingFetch())
    const timedOut = track(httpJson(CFG, '/x'))
    await vi.advanceTimersByTimeAsync(TIMEOUT_BOUND_MS)
    expect(timedOut.outcome().settled).toBe(true)
    expect(vi.getTimerCount(), 'timeout').toBe(0)

    const external = new AbortController()
    const cancelled = track(httpJson(CFG, '/x', { signal: external.signal }))
    external.abort()
    await vi.advanceTimersByTimeAsync(0)
    expect(cancelled.outcome().settled).toBe(true)
    expect(vi.getTimerCount(), 'caller abort').toBe(0)
  })

  it('the caller signal listener is released after the call', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => OK()))
    const external = new AbortController()
    const remove = vi.spyOn(external.signal, 'removeEventListener')

    await httpJson(CFG, '/x', { signal: external.signal })

    expect(remove).toHaveBeenCalledWith('abort', expect.any(Function))
  })
})
