/**
 * 034 S5a, review round 1, items 1 and 3: what an Interact action does with a request id and with a timeout.
 * The REAL `simulatorApi`/`http` run here; only `fetch` is a stub, and the clock is controlled.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import { ApiError, LONG_REQUEST_TIMEOUT_MS } from '../api/http'
import { clearingRefusalText, paymentRefusalText } from '../utils/paymentRefusalText'
import { isInteractActionError, useInteractActions, type InteractActionError } from './useInteractActions'

function actions() {
  return useInteractActions({ httpConfig: ref({ apiBase: '/api/v1' }), runId: ref('run-1') })
}

function hangingFetch() {
  return vi.fn((_url: unknown, init?: RequestInit) => {
    return new Promise<Response>((_resolve, reject) => {
      const signal = init?.signal
      signal?.addEventListener('abort', () => reject(signal.reason ?? new DOMException('Aborted', 'AbortError')), { once: true })
    })
  })
}

/** What the simulator actions answer on a refusal: a FLAT body (no `request_id`), the id only in the header. */
function refusal409(headers: Record<string, string>): Response {
  return new Response(
    JSON.stringify({ code: 'NO_ROUTE', message: 'no route', details: { reason: 'no_route', max_available: '3.00' } }),
    { status: 409, headers: { 'Content-Type': 'application/json', ...headers } },
  )
}

async function rejection<T>(p: Promise<T>): Promise<InteractActionError> {
  try {
    await p
  } catch (e) {
    if (isInteractActionError(e)) return e
    throw e
  }
  throw new Error('expected the action to reject')
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  document.documentElement.lang = ''
})

describe('item 1: the request id survives the Interact path', () => {
  it('a 409 with X-Request-ID and a flat body: the mapped error carries it and the refusal texts show (ref: ...)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => refusal409({ 'X-Request-ID': 'req-409' })))

    const call = rejection(actions().sendPayment('a', 'b', '1.00', 'UAH'))
    await vi.advanceTimersByTimeAsync(0)
    const error = await call

    expect(error.code).toBe('NO_ROUTE')
    expect(error.requestId).toBe('req-409')
    const text = paymentRefusalText(error, 'UAH', 'en')
    expect(text).toContain('No payment route')
    expect(text).toContain('(ref: req-409)')
    expect(clearingRefusalText(error, 'UAH', 'en')).toContain('(ref: req-409)')
  })

  it('a refusal the client has no text for keeps the server message and gets the ref too', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ code: 'SOMETHING_NEW', message: 'server words' }), {
          status: 409,
          headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'req-new' },
        }),
      ),
    )
    const call = rejection(actions().createTrustline('a', 'b', '10', 'UAH'))
    await vi.advanceTimersByTimeAsync(0)

    const text = paymentRefusalText(await call, 'UAH', 'en')
    expect(text).toContain('server words')
    expect(text).toContain('(ref: req-new)')
  })

  it('counter-check: no id, no ref', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => refusal409({})))
    const call = rejection(actions().sendPayment('a', 'b', '1.00', 'UAH'))
    await vi.advanceTimersByTimeAsync(0)
    const error = await call

    expect(error.requestId ?? null).toBeNull()
    expect(paymentRefusalText(error, 'UAH', 'en')).not.toContain('(ref:')
  })
})

describe('fix-delta item 5: a 2xx body that is not JSON is never read as a business refusal', () => {
  it('a diagnostic excerpt that happens to parse as an error envelope stays INVALID_JSON', async () => {
    // A valid refusal envelope padded with spaces to the 500 characters the transport keeps as an excerpt,
    // followed by garbage: the WHOLE body is not JSON, the excerpt alone is.
    const body = `${'{"code":"NO_ROUTE","message":"no route"}'.padEnd(500, ' ')}<garbage`
    vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status: 200, headers: { 'X-Request-ID': 'req-trap' } })))

    const call = rejection(actions().sendPayment('a', 'b', '1.00', 'UAH'))
    await vi.advanceTimersByTimeAsync(0)
    const error = await call

    expect(error.code, 'the transport failure was turned into the refusal the excerpt spells').toBe('INVALID_JSON')
    expect(paymentRefusalText(error, 'UAH', 'en')).not.toContain('No payment route')
    expect(error.message, 'the message of the excerpt replaced the transport message').not.toBe('no route')
    expect(error.message).toMatch(/not valid JSON/)
    expect(error.requestId).toBe('req-trap')
  })

  it('anti-vacuum: a real 409 refusal envelope is still read from the body', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => refusal409({})))
    const call = rejection(actions().sendPayment('a', 'b', '1.00', 'UAH'))
    await vi.advanceTimersByTimeAsync(0)

    expect((await call).code).toBe('NO_ROUTE')
  })
})

describe('item 3: a timeout of a mutating action is an unknown outcome, not a refusal', () => {
  const MUTATING: Array<[string, () => Promise<unknown>]> = [
    ['sendPayment', () => actions().sendPayment('a', 'b', '1.00', 'UAH')],
    ['runClearing', () => actions().runClearing('UAH')],
    ['createTrustline', () => actions().createTrustline('a', 'b', '10', 'UAH')],
    ['updateTrustline', () => actions().updateTrustline('a', 'b', '20', 'UAH')],
    ['closeTrustline', () => actions().closeTrustline('a', 'b', 'UAH')],
  ]

  it.each(MUTATING)('%s: the text says the result is unknown and the action may have been carried out', async (_name, run) => {
    vi.stubGlobal('fetch', hangingFetch())
    const call = rejection(run())

    await vi.advanceTimersByTimeAsync(LONG_REQUEST_TIMEOUT_MS)
    const error = await call

    expect(error.code).toBe('TIMEOUT')
    expect(error.message).toMatch(/unknown/i)
    expect(error.message).toMatch(/may have been/i)
    expect(error.message).toContain(String(LONG_REQUEST_TIMEOUT_MS / 1000))
    expect(error.message).not.toMatch(/->/)
  })

  it('the payment text on a timeout is neither the engine "timed out" refusal nor the generic refusal', async () => {
    vi.stubGlobal('fetch', hangingFetch())
    const call = rejection(actions().sendPayment('a', 'b', '1.00', 'UAH'))
    await vi.advanceTimersByTimeAsync(LONG_REQUEST_TIMEOUT_MS)
    const error = await call

    const en = paymentRefusalText(error, 'UAH', 'en')
    expect(en).toMatch(/unknown/i)
    expect(en).not.toBe('The payment timed out.')
    expect(en).not.toBe('The payment was not accepted.')
    expect(en).not.toBe('The payment was refused.')
    expect(clearingRefusalText(error, 'UAH', 'en')).toMatch(/unknown/i)
  })

  it('the Russian locale gets the Russian text', async () => {
    document.documentElement.lang = 'ru'
    vi.stubGlobal('fetch', hangingFetch())
    const call = rejection(actions().sendPayment('a', 'b', '1.00', 'UAH'))
    await vi.advanceTimersByTimeAsync(LONG_REQUEST_TIMEOUT_MS)
    const error = await call

    expect(error.message).toContain('результат неизвестен')
    expect(error.message).toContain('могло быть выполнено')
    expect(error.message).toContain('120')
  })

  it('the rejection is the mapped action error, never the raw ApiError (which has status/code/message too)', async () => {
    vi.stubGlobal('fetch', hangingFetch())
    const timedOut = rejection(actions().sendPayment('a', 'b', '1.00', 'UAH'))
    await vi.advanceTimersByTimeAsync(LONG_REQUEST_TIMEOUT_MS)
    expect(await timedOut).not.toBeInstanceOf(ApiError)

    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>proxy</html>', { status: 200, headers: { 'X-Request-ID': 'req-html' } })))
    const notJson = rejection(actions().sendPayment('a', 'b', '1.00', 'UAH'))
    await vi.advanceTimersByTimeAsync(0)
    const error = await notJson
    expect(error).not.toBeInstanceOf(ApiError)
    expect(error.code).toBe('INVALID_JSON')
    expect(error.requestId).toBe('req-html')
  })

  it('anti-vacuum: a READ timeout is not an unknown outcome (the client asked for no change; the lazy seeding some reads trigger server-side is not an action to repeat), and keeps the technical message', async () => {
    vi.stubGlobal('fetch', hangingFetch())
    const call = rejection(actions().fetchTrustlines('UAH'))

    await vi.advanceTimersByTimeAsync(LONG_REQUEST_TIMEOUT_MS)
    const error = await call

    expect(error.code).toBe('TIMEOUT')
    expect(error.message).toMatch(/timeout after/)
    expect(error.message).not.toMatch(/unknown/i)
  })

  it('anti-vacuum: a real refusal of a mutating action keeps its own text', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => refusal409({})))
    const call = rejection(actions().sendPayment('a', 'b', '1.00', 'UAH'))
    await vi.advanceTimersByTimeAsync(0)
    const error = await call

    expect(paymentRefusalText(error, 'UAH', 'en')).toBe('No payment route between these participants (available now: 3.00 UAH).')
  })
})
