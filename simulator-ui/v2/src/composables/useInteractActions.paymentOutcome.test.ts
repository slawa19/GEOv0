/**
 * 037 A2: what a payment request that was SENT says about its outcome, through the real transport and the real mapper
 * (only `fetch` is a stand-in). The server's verdicts and the lack of one are told apart here; the life of the key on top
 * of them is `useInteractMode.manualPayment.test.ts`.
 */
import { ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useInteractActions } from './useInteractActions'

afterEach(() => {
  vi.unstubAllGlobals()
})

const OK_BODY = {
  ok: true, payment_id: 'man:abc', from_pid: 'alice', to_pid: 'bob', equivalent: 'UAH', amount: '5.00',
  status: 'COMMITTED', routes: [{ hops: [{ from: 'alice', to: 'bob', amount: '5.00' }] }],
}

function actions(runId = 'run_1') {
  return useInteractActions({ httpConfig: ref({ apiBase: 'http://example.test', accessToken: 'x' }), runId: ref(runId) })
}

function answer(status: number, body: unknown) {
  return vi.fn(async () => new Response(typeof body === 'string' ? body : JSON.stringify(body), {
    status, headers: { 'content-type': 'application/json' },
  }))
}

async function failure(promise: Promise<unknown>) {
  try {
    await promise
  } catch (e) {
    return e as { status: number; code: string; outcomeUnknown?: boolean; details?: Record<string, unknown> | null }
  }
  throw new Error('expected the payment to fail')
}

describe('sendPayment: unknown outcome is told from a verdict', () => {
  it('puts the idempotency key on the wire when it is given, and only then', async () => {
    const f = answer(200, OK_BODY)
    vi.stubGlobal('fetch', f)
    await actions().sendPayment('alice', 'bob', '5.00', 'UAH', { idempotencyKey: 'k-1' })
    await actions().sendPayment('alice', 'bob', '5.00', 'UAH')
    const bodies = f.mock.calls.map((c) => JSON.parse(String((c as unknown as [string, RequestInit])[1].body)))
    expect(bodies[0].idempotency_key).toBe('k-1')
    expect('idempotency_key' in bodies[1]).toBe(false)
  })

  it('a network failure after sending is unknown', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
    const e = await failure(actions().sendPayment('alice', 'bob', '5.00', 'UAH', { idempotencyKey: 'k-1' }))
    expect(e.outcomeUnknown).toBe(true)
  })

  it('a 2xx answer that is not JSON is unknown (the server may have made the payment)', async () => {
    vi.stubGlobal('fetch', answer(200, 'not json at all'))
    const e = await failure(actions().sendPayment('alice', 'bob', '5.00', 'UAH', { idempotencyKey: 'k-1' }))
    expect(e.outcomeUnknown).toBe(true)
  })

  it('a 2xx answer that breaks the response contract is unknown', async () => {
    const { routes: _routes, ...withoutRoutes } = OK_BODY
    vi.stubGlobal('fetch', answer(200, withoutRoutes))
    const e = await failure(actions().sendPayment('alice', 'bob', '5.00', 'UAH', { idempotencyKey: 'k-1' }))
    expect(e.outcomeUnknown).toBe(true)
  })

  it('a cancel after sending (abort) is unknown', async () => {
    const ctrl = new AbortController()
    vi.stubGlobal('fetch', vi.fn((_u: unknown, init?: RequestInit) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    })))
    const pending = failure(actions().sendPayment('alice', 'bob', '5.00', 'UAH', { idempotencyKey: 'k-1', signal: ctrl.signal }))
    await Promise.resolve()
    ctrl.abort()
    expect((await pending).outcomeUnknown).toBe(true)
  })

  it('503 ENGINE_TIMEOUT without a verdict on the key is unknown; with "spent" it is a verdict', async () => {
    vi.stubGlobal('fetch', answer(503, { code: 'ENGINE_TIMEOUT', message: 'Payment timed out', details: { reason: 'timeout', idempotency_key_spent: false } }))
    expect((await failure(actions().sendPayment('alice', 'bob', '5.00', 'UAH', { idempotencyKey: 'k' }))).outcomeUnknown).toBe(true)
    vi.stubGlobal('fetch', answer(503, { code: 'ENGINE_TIMEOUT', message: 'Payment timeout', details: { reason: 'timeout', idempotency_key_spent: true } }))
    const spent = await failure(actions().sendPayment('alice', 'bob', '5.00', 'UAH', { idempotencyKey: 'k' }))
    expect(spent.outcomeUnknown).toBe(false)
    expect(spent.details?.idempotency_key_spent).toBe(true)
  })

  it.each([
    ['409 NO_ROUTE', 409, { code: 'NO_ROUTE', message: 'no', details: { reason: 'no_route' } }],
    ['400 INVALID_REQUEST', 400, { code: 'INVALID_REQUEST', message: 'bad', details: {} }],
    ['403 ACCESS_DENIED', 403, { code: 'ACCESS_DENIED', message: 'no', details: {} }],
  ])('%s is a refusal, not an unknown outcome', async (_what, status, body) => {
    vi.stubGlobal('fetch', answer(status, body))
    expect((await failure(actions().sendPayment('alice', 'bob', '5.00', 'UAH', { idempotencyKey: 'k' }))).outcomeUnknown).toBe(false)
  })

  it('a request that never left (no run id) is a plain failure, not an unknown outcome', async () => {
    const f = vi.fn()
    vi.stubGlobal('fetch', f)
    const e = await failure(actions('').sendPayment('alice', 'bob', '5.00', 'UAH', { idempotencyKey: 'k' }))
    expect(e.outcomeUnknown).toBe(false)
    expect(f).not.toHaveBeenCalled()
  })
})
