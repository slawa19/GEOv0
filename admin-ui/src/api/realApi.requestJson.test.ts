import { afterEach, describe, expect, it, vi } from 'vitest'
import { z } from 'zod'

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

    await expect(requestJson('/api/v1/health', { toast: false })).rejects.toMatchObject({
      name: 'ApiException',
      code: 'INVALID_JSON',
    })
  })

  it('throws ApiException(INVALID_JSON) when res.ok but body is empty', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''

    const fetchMock = vi.fn(async () => new Response('', { status: 200, statusText: 'OK' }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    await expect(requestJson('/api/v1/health', { toast: false })).rejects.toMatchObject({
      name: 'ApiException',
      code: 'INVALID_JSON',
    })
  })

  it('does not throw INVALID_JSON for 204 No Content', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''

    const fetchMock = vi.fn(async () => new Response(null, { status: 204, statusText: 'No Content' }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    await expect(requestJson<unknown>('/api/v1/health', { toast: false })).resolves.toBeUndefined()
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

    await expect(requestJson<typeof payload>('/api/v1/health', { toast: false })).resolves.toEqual(payload)
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

    await expect(requestJson('/api/v1/health', { timeoutMs: 5, toast: false })).rejects.toMatchObject({
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
      requestJson('/api/v1/health', { schema: z.object({ n: z.number() }), toast: false }),
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
      requestJson('/api/v1/health', { schema: z.object({ n: z.number() }), toast: false }),
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
