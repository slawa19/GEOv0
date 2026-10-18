/**
 * 034 S5a, review round 1, items 1 and 3: what an Interact action does with a request id and with a timeout.
 * The REAL `simulatorApi`/`http` run here; only `fetch` is a stub, and the clock is controlled.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'

import { ApiError, LONG_REQUEST_TIMEOUT_MS } from '../api/http'
import { setUiLocale } from '../i18n/locale'
import { t } from '../i18n/t'
import { actionErrorText, clearingRefusalText, paymentRefusalText } from '../utils/paymentRefusalText'
import { useInteractMode } from './useInteractMode'
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

  // 046 wave 2 (decision 3): was "keeps the server message" - a code the client has no text for is told by the general
  // text WITH the code (the server's English is no longer shown); the reference is kept.
  it('a refusal the client has no text for shows the general text with its code and gets the ref too', async () => {
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
    expect(text).toBe('The action failed (SOMETHING_NEW). (ref: req-new)')
    expect(text).not.toContain('server words')
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

// Codex §15 round 1 (P2): a value the client threw (not an HTTP answer) is marked as the client's own message, so a SERVER
// answer that happens to carry the code `UNKNOWN` is no longer taken for one.
describe('046 round 1: the client marks its own failures', () => {
  it('a missing run id is the client\'s own message (clientMessage), in the language of the interface', async () => {
    const a = useInteractActions({ httpConfig: ref({ apiBase: '/api/v1' }), runId: ref('') })
    const e = await rejection(a.createTrustline('a', 'b', '10', 'UAH'))
    expect(e.code).toBe('UNKNOWN')
    expect(e.clientMessage).toBe(true)
    expect(e.message).toBe('No active run_id')
  })
  it('counter-check: a server answer with the code UNKNOWN is not marked', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ code: 'UNKNOWN', message: '' }), { status: 500, headers: { 'Content-Type': 'application/json' } })))
    const e = await rejection(actions().createTrustline('a', 'b', '10', 'UAH'))
    expect(e.code).toBe('UNKNOWN')
    expect(e.clientMessage).toBeUndefined()
  })
})

// Codex §15 fix-delta (F2, 2026-10-18): every non-HTTP rejection was marked as the client's own text, so a network failure
// showed the browser's English "Failed to fetch" in the trust line and clearing windows under ?lang=ru. Only a text the
// client composed from its dictionary passes as its own; a network failure is told in the language of the interface.
describe('046 fix-delta F2: a network failure is told in the language of the interface', () => {
  const failingFetch = () => vi.fn(async () => { throw new TypeError('Failed to fetch') })
  const im = () => useInteractMode({ actions: actions(), runId: computed(() => 'run-1'), equivalent: computed(() => 'UAH'), snapshot: ref(null) })

  afterEach(() => setUiLocale('en'))

  it('the trust line window: no "Failed to fetch", the Russian text of a lost connection', async () => {
    setUiLocale('ru')
    vi.stubGlobal('fetch', failingFetch())
    const mode = im()
    mode.startTrustlineFlow()
    mode.selectNode('alice')
    mode.selectNode('bob')
    await mode.confirmTrustlineCreate('10')
    expect(mode.state.error).toBe(t('actionError.NETWORK', {}, 'ru'))
    expect(mode.state.error).not.toContain('Failed to fetch')
  })

  it('the clearing window: the same', async () => {
    setUiLocale('ru')
    vi.stubGlobal('fetch', failingFetch())
    const mode = im()
    mode.startClearingFlow()
    await mode.confirmClearing()
    expect(mode.state.clearingFailure).toBe(t('actionError.NETWORK', {}, 'ru'))
    expect(mode.state.error).not.toContain('Failed to fetch')
  })

  it('another thrown value is the general text with a code, never its own English message; the client\'s own text passes', async () => {
    const a = actions()
    vi.stubGlobal('fetch', vi.fn(async () => { throw new DOMException('The operation was aborted.', 'AbortError') }))
    const aborted = await rejection(a.createTrustline('a', 'b', '10', 'UAH'))
    expect(aborted.clientMessage).toBeUndefined()
    expect(actionErrorText(aborted, 'ru')).toBe(t('actionError.unknown', { code: 'UNKNOWN' }, 'ru'))
    // Counter-check: the text the client composed (a missing run id) is still its own.
    setUiLocale('ru')
    const own = await rejection(useInteractActions({ httpConfig: ref({ apiBase: '/api/v1' }), runId: ref('') }).createTrustline('a', 'b', '10', 'UAH'))
    expect(own.clientMessage).toBe(true)
    expect(actionErrorText(own, 'ru')).toBe(t('actionError.noRun', {}, 'ru'))
  })
})
