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
      new Response(JSON.stringify({ error: { code: 'EXPECTED', message: 'stop' } }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
    const threshold = '0.10000000000000001'

    // Only the URL is under test; the stub refuses every call, so each one rejects.
    await realApi.trustlineBottlenecks({ threshold }).catch(() => undefined)
    await realApi.liquiditySummary({ threshold }).catch(() => undefined)
    await realApi.participantMetrics('PID_A', { threshold }).catch(() => undefined)

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      `/api/v1/admin/trustlines/bottlenecks?threshold=${threshold}&limit=10`,
      `/api/v1/admin/liquidity/summary?threshold=${threshold}&limit=10`,
      `/api/v1/admin/participants/PID_A/metrics?threshold=${threshold}`,
    ])
  })
})

describe('realApi liquidity summary decoder (028 F-028-37)', () => {
  it('accepts the summary without an equivalent: money null, net lists empty', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    const body = {
      equivalent: null, threshold: 0.1, updated_at: '2026-10-04T00:00:00Z', active_trustlines: 2, bottlenecks: 0,
      incidents_over_sla: 0, total_limit: null, total_used: null, total_available: null,
      top_creditors: [], top_debtors: [], top_by_abs_net: [], top_bottleneck_edges: [],
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
