import { afterEach, describe, expect, it, vi } from 'vitest'
import { z } from 'zod'

import { DEFAULT_REQUEST_TIMEOUT_MS, HEALTH_REQUEST_TIMEOUT_MS, LONG_REQUEST_TIMEOUT_MS } from '../constants/timing'
import { ApiException } from './apiException'
import { realApi, requestJson } from './realApi'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('realApi.requestJson', () => {
  it('throws ApiException(INVALID_JSON) when res.ok but body is not valid JSON', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''

    const fetchMock = vi.fn(async () => new Response('not-json', { status: 200, statusText: 'OK' }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    await expect(requestJson('/api/v1/health')).rejects.toMatchObject({
      name: 'ApiException',
      code: 'INVALID_JSON',
    })
  })

  it('throws ApiException(INVALID_JSON) when res.ok but body is empty', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''

    const fetchMock = vi.fn(async () => new Response('', { status: 200, statusText: 'OK' }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    await expect(requestJson('/api/v1/health')).rejects.toMatchObject({
      name: 'ApiException',
      code: 'INVALID_JSON',
    })
  })

  it('does not throw INVALID_JSON for 204 No Content', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''

    const fetchMock = vi.fn(async () => new Response(null, { status: 204, statusText: 'No Content' }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    await expect(requestJson<unknown>('/api/v1/health')).resolves.toBeUndefined()
  })

  it('returns the body as is, even one that has a success field (there is no envelope)', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''

    const payload = { success: true, value: 123 }
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(payload), {
        status: 200,
        statusText: 'OK',
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    await expect(requestJson<typeof payload>('/api/v1/health')).resolves.toEqual(payload)
  })

  it('throws ApiException(TIMEOUT) when fetch is aborted by timeoutMs', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''

    const fetchMock = vi.fn((_: unknown, init?: { signal?: AbortSignal }) => {
        return new Promise<Response>((_, reject) => {
          const sig = init?.signal
          if (!sig) {
            reject(new Error('Missing signal'))
            return
          }

          const onAbort = () => reject(new DOMException('Aborted', 'AbortError'))
          if (sig.aborted) {
            onAbort()
            return
          }
          sig.addEventListener('abort', onAbort, { once: true })
        })
      })
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    await expect(requestJson('/api/v1/health', { timeoutMs: 5 })).rejects.toMatchObject({
      name: 'ApiException',
      code: 'TIMEOUT',
    })
  })

  it('validates the body with schema when provided', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''

    const fetchMock = vi.fn(async () =>
        new Response(JSON.stringify({ n: 123 }), {
          status: 200,
          statusText: 'OK',
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    await expect(requestJson('/api/v1/health', { schema: z.object({ n: z.number() }) })).resolves.toEqual({ n: 123 })
  })

  it('throws ApiException(INVALID_RESPONSE) when schema validation fails', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''

    const fetchMock = vi.fn(async () =>
        new Response(JSON.stringify({ n: 'oops' }), {
          status: 200,
          statusText: 'OK',
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    await expect(
      requestJson('/api/v1/health', { schema: z.object({ n: z.number() }) }),
    ).rejects.toMatchObject({
      name: 'ApiException',
      code: 'INVALID_RESPONSE',
    })
  })

  it('gives an old {success, data} envelope no meaning: it is data, and the schema refuses it', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''

    const fetchMock = vi.fn(async () =>
        new Response(JSON.stringify({ success: true, data: { n: 123 } }), {
          status: 200,
          statusText: 'OK',
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    await expect(
      requestJson('/api/v1/health', { schema: z.object({ n: z.number() }) }),
    ).rejects.toMatchObject({ name: 'ApiException', code: 'INVALID_RESPONSE' })
  })
})

// 032 S5 (F-1, F-2, F-4, A-4): the bottleneck list, the incidents list, admin abort and the admin cycle search were
// removed with their server routes, and the two narrowed reads lost their `threshold` (the old threshold-transport
// cases tested that removed parameter). What the client may still send is pinned here by the URL it builds.
describe('realApi narrowed admin reads (032 S5)', () => {
  it('has no client for a removed route', () => {
    for (const removed of ['trustlineBottlenecks', 'listIncidents', 'abortTx', 'clearingCycles']) {
      expect(removed in realApi, removed).toBe(false)
    }
  })

  it('asks the summary and the metrics with the equivalent only', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify({ error: { code: 'EXPECTED', message: 'stop' } }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    // Only the URL is under test; the stub refuses every call, so each one rejects.
    await realApi.liquiditySummary({ equivalent: 'UAH' }).catch(() => undefined)
    await realApi.liquiditySummary({}).catch(() => undefined)
    await realApi.participantMetrics('PID_A', { equivalent: 'UAH' }).catch(() => undefined)
    await realApi.participantMetrics('PID_A').catch(() => undefined)

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      '/api/v1/admin/liquidity/summary?equivalent=UAH',
      '/api/v1/admin/liquidity/summary',
      '/api/v1/admin/participants/PID_A/metrics?equivalent=UAH',
      '/api/v1/admin/participants/PID_A/metrics',
    ])
  })
})

describe('realApi liquidity summary decoder (028 F-028-37)', () => {
  it('accepts the summary without an equivalent: money null', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    const body = {
      equivalent: null, updated_at: '2026-10-04T00:00:00Z', active_trustlines: 2,
      total_limit: null, total_used: null, total_available: null,
    }
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })),
    )

    const summary = await realApi.liquiditySummary({})

    expect([summary.total_limit, summary.total_used, summary.total_available]).toEqual([null, null, null])
    expect(summary.active_trustlines).toBe(2)
  })
})

// 032 S6 (E-1): the error the operator sees must carry the correlation id the server already sends
// (AGENTS §12): in the error body (`error.request_id`) and in the `X-Request-ID` response header.
describe('realApi.requestJson: request_id of an error (032 S6, E-1)', () => {
  function stubFetch(response: Response) {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    vi.stubGlobal('fetch', vi.fn(async () => response) as unknown as typeof fetch)
  }

  it('takes request_id from the error body', async () => {
    stubFetch(
      new Response(JSON.stringify({ error: { code: 'E010', message: 'Internal error', request_id: 'req-body-1' } }), {
        status: 500,
        statusText: 'Internal Server Error',
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    await expect(requestJson('/api/v1/health')).rejects.toMatchObject({
      name: 'ApiException',
      code: 'E010',
      requestId: 'req-body-1',
    })
  })

  it('falls back to the X-Request-ID header when the body has none', async () => {
    stubFetch(
      new Response('upstream exploded', { status: 502, statusText: 'Bad Gateway', headers: { 'X-Request-ID': 'req-hdr-2' } }),
    )
    await expect(requestJson('/api/v1/health')).rejects.toMatchObject({ code: 'HTTP_ERROR', requestId: 'req-hdr-2' })
  })

  it('has no request id when the server sent none (null, not an empty string)', async () => {
    stubFetch(new Response(JSON.stringify({ error: { code: 'E1', message: 'x' } }), { status: 400 }))
    const e = await requestJson('/api/v1/health').catch((err: unknown) => err)
    expect(e).toBeInstanceOf(ApiException)
    expect((e as ApiException).requestId).toBeNull()
  })

  it('carries the id on the decoder failures too (INVALID_RESPONSE)', async () => {
    stubFetch(new Response(JSON.stringify({ n: 'oops' }), { status: 200, headers: { 'X-Request-ID': 'req-ok-3' } }))
    await expect(requestJson('/api/v1/health', { schema: z.object({ n: z.number() }) })).rejects.toMatchObject({
      code: 'INVALID_RESPONSE',
      requestId: 'req-ok-3',
    })
  })
})

// 032 S6 (E-2): a hung request ends with an error. The bound covers the headers AND the body read, and the
// fetch implementation is not trusted to honour the abort signal.
describe('realApi.requestJson: timeout (032 S6, E-2)', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  const hangs = () => new Promise<Response>(() => {})

  function noBase() {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
  }

  it('ends a request that never answers with TIMEOUT after the default bound', async () => {
    vi.useFakeTimers()
    noBase()
    vi.stubGlobal('fetch', vi.fn(hangs) as unknown as typeof fetch)

    let settled: unknown = 'pending'
    const run = requestJson('/api/v1/health').catch((e: unknown) => {
      settled = e
    })
    await vi.advanceTimersByTimeAsync(DEFAULT_REQUEST_TIMEOUT_MS - 1)
    expect(settled).toBe('pending')
    await vi.advanceTimersByTimeAsync(1)
    await run
    expect(settled).toMatchObject({ name: 'ApiException', code: 'TIMEOUT' })
  })

  it('ends a request whose body never arrives (headers came, the body read hangs) with TIMEOUT', async () => {
    vi.useFakeTimers()
    noBase()
    const stalledBody = {
      ok: true,
      status: 200,
      statusText: 'OK',
      headers: new Headers(),
      text: () => new Promise<string>(() => {}),
    } as unknown as Response
    vi.stubGlobal('fetch', vi.fn(async () => stalledBody) as unknown as typeof fetch)

    const run = expect(requestJson('/api/v1/health')).rejects.toMatchObject({ code: 'TIMEOUT' })
    await vi.advanceTimersByTimeAsync(DEFAULT_REQUEST_TIMEOUT_MS)
    await run
  })

  it('gives the health probes a shorter bound than any other request', async () => {
    vi.useFakeTimers()
    noBase()
    vi.stubGlobal('fetch', vi.fn(hangs) as unknown as typeof fetch)

    expect(HEALTH_REQUEST_TIMEOUT_MS).toBeLessThan(DEFAULT_REQUEST_TIMEOUT_MS)
    for (const probe of [() => realApi.health(), () => realApi.healthDb()]) {
      const run = expect(probe()).rejects.toMatchObject({ code: 'TIMEOUT' })
      await vi.advanceTimersByTimeAsync(HEALTH_REQUEST_TIMEOUT_MS)
      await run
    }
    // The same elapsed time does not end an ordinary request: the short bound is not a global one.
    let ordinary: unknown = 'pending'
    const other = requestJson('/api/v1/admin/participants').catch((e: unknown) => {
      ordinary = e
    })
    await vi.advanceTimersByTimeAsync(HEALTH_REQUEST_TIMEOUT_MS)
    expect(ordinary).toBe('pending')
    await vi.advanceTimersByTimeAsync(DEFAULT_REQUEST_TIMEOUT_MS)
    await other
    expect(ordinary).toMatchObject({ code: 'TIMEOUT' })
  })

  it('lets the two reconciliation-running actions wait longer than an ordinary request', async () => {
    vi.useFakeTimers()
    noBase()
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_ADMIN_TOKEN = 'test-token'
    vi.stubGlobal('fetch', vi.fn(hangs) as unknown as typeof fetch)

    expect(LONG_REQUEST_TIMEOUT_MS).toBeGreaterThan(DEFAULT_REQUEST_TIMEOUT_MS)
    for (const action of [() => realApi.integrityVerify(), () => realApi.clearIntegrityHold('UAH', 'recheck')]) {
      let outcome: unknown = 'pending'
      const run = action().catch((e: unknown) => {
        outcome = e
      })
      await vi.advanceTimersByTimeAsync(DEFAULT_REQUEST_TIMEOUT_MS)
      expect(outcome).toBe('pending')
      await vi.advanceTimersByTimeAsync(LONG_REQUEST_TIMEOUT_MS - DEFAULT_REQUEST_TIMEOUT_MS)
      await run
      expect(outcome).toMatchObject({ code: 'TIMEOUT' })
    }
  })

  it('does not kill a request that answered: no timer is left once the body is read', async () => {
    vi.useFakeTimers()
    noBase()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })) as unknown as typeof fetch,
    )

    await expect(requestJson('/api/v1/health')).resolves.toEqual({ ok: true })
    expect(vi.getTimerCount()).toBe(0)
  })
})

// 032 S6 (E-3): one place raises the toast, and it is not the transport. A failed request used to be toasted by
// `requestJson` AND by the caller's own catch - two toasts for one failure. The caller (page, store) decides how
// the error is shown, with `describeError`.
describe('realApi.requestJson: no toast of its own (032 S6, E-3)', () => {
  it('rejects without raising a message', async () => {
    const { ElMessage } = await import('element-plus')
    const error = vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ error: { code: 'E1', message: 'x' } }), { status: 500 })) as unknown as typeof fetch,
    )

    await expect(requestJson('/api/v1/health')).rejects.toMatchObject({ code: 'E1' })
    await new Promise((r) => setTimeout(r, 0))
    expect(error).not.toHaveBeenCalled()
  })
})
