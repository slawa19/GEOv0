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

    const env = await requestJson<unknown>('/api/v1/health', { toast: false })
    expect(env).toEqual({ success: true, data: undefined })
  })

  it('wraps non-envelope payloads that merely contain a success field', async () => {
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

    const env = await requestJson<typeof payload>('/api/v1/health', { toast: false })
    expect(env.success).toBe(true)
    if (!env.success) return
    expect(env.data).toEqual(payload)
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

  it('validates envelope.data with schema when provided', async () => {
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

    const env = await requestJson('/api/v1/health', { schema: z.object({ n: z.number() }) })
    expect(env.success).toBe(true)
    if (!env.success) return
    expect(env.data).toEqual({ n: 123 })
  })

  it('throws ApiException(INVALID_RESPONSE) when schema validation fails', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''

    const fetchMock = vi.fn(async () =>
        new Response(JSON.stringify({ success: true, data: { n: 'oops' } }), {
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

  it('does not block success:false envelopes even if schema is provided', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''

    const fetchMock = vi.fn(async () =>
        new Response(JSON.stringify({ success: false, error: { code: 'NOPE', message: 'Nope' } }), {
          status: 200,
          statusText: 'OK',
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    const env = await requestJson('/api/v1/health', { schema: z.object({ n: z.number() }), toast: false })
    expect(env.success).toBe(false)
  })
})

describe('realApi bottleneck threshold transport', () => {
  it('rejects invalid thresholds before any HTTP request', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    expect(() => realApi.trustlineBottlenecks({ threshold: '1.00000000000000001' })).toThrowError(
      expect.objectContaining({ name: 'ApiException', code: 'VALIDATION_ERROR', status: 422 }),
    )
    expect(() => realApi.liquiditySummary({ threshold: '-0.01' })).toThrowError(
      expect.objectContaining({ name: 'ApiException', code: 'VALIDATION_ERROR', status: 422 }),
    )
    await expect(realApi.participantMetrics('PID_A', { threshold: '1e-1' })).rejects.toMatchObject({
      name: 'ApiException',
      code: 'VALIDATION_ERROR',
      status: 422,
    })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('preserves a valid high-precision decimal string for every endpoint', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify({ success: false, error: { code: 'EXPECTED', message: 'stop' } }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
    const threshold = '0.10000000000000001'

    await realApi.trustlineBottlenecks({ threshold })
    await realApi.liquiditySummary({ threshold })
    await realApi.participantMetrics('PID_A', { threshold })

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      `/api/v1/admin/trustlines/bottlenecks?threshold=${threshold}&limit=10`,
      `/api/v1/admin/liquidity/summary?threshold=${threshold}&limit=10`,
      `/api/v1/admin/participants/PID_A/metrics?threshold=${threshold}`,
    ])
  })
})
