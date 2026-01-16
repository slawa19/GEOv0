import { afterEach, describe, expect, it, vi } from 'vitest'
import { z } from 'zod'

import { requestJson } from './realApi'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('realApi.requestJson', () => {
  it('throws ApiException(INVALID_JSON) when res.ok but body is not valid JSON', async () => {
    ;(import.meta as any).env.VITE_API_BASE_URL = ''

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('not-json', { status: 200, statusText: 'OK' })) as any,
    )

    await expect(requestJson('/api/v1/health', { toast: false })).rejects.toMatchObject({
      name: 'ApiException',
      code: 'INVALID_JSON',
    })
  })

  it('throws ApiException(INVALID_JSON) when res.ok but body is empty', async () => {
    ;(import.meta as any).env.VITE_API_BASE_URL = ''

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('', { status: 200, statusText: 'OK' })) as any,
    )

    await expect(requestJson('/api/v1/health', { toast: false })).rejects.toMatchObject({
      name: 'ApiException',
      code: 'INVALID_JSON',
    })
  })

  it('throws ApiException(TIMEOUT) when fetch is aborted by timeoutMs', async () => {
    ;(import.meta as any).env.VITE_API_BASE_URL = ''

    vi.stubGlobal(
      'fetch',
      vi.fn((_: unknown, init?: { signal?: AbortSignal }) => {
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
      }) as any,
    )

    await expect(requestJson('/api/v1/health', { timeoutMs: 5, toast: false })).rejects.toMatchObject({
      name: 'ApiException',
      code: 'TIMEOUT',
    })
  })

  it('validates envelope.data with schema when provided', async () => {
    ;(import.meta as any).env.VITE_API_BASE_URL = ''

    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ success: true, data: { n: 123 } }), {
          status: 200,
          statusText: 'OK',
          headers: { 'Content-Type': 'application/json' },
        }),
      ) as any,
    )

    const env = await requestJson('/api/v1/health', { schema: z.object({ n: z.number() }) })
    expect(env.success).toBe(true)
    if (!env.success) return
    expect(env.data).toEqual({ n: 123 })
  })

  it('throws ApiException(INVALID_RESPONSE) when schema validation fails', async () => {
    ;(import.meta as any).env.VITE_API_BASE_URL = ''

    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ success: true, data: { n: 'oops' } }), {
          status: 200,
          statusText: 'OK',
          headers: { 'Content-Type': 'application/json' },
        }),
      ) as any,
    )

    await expect(
      requestJson('/api/v1/health', { schema: z.object({ n: z.number() }), toast: false }),
    ).rejects.toMatchObject({
      name: 'ApiException',
      code: 'INVALID_RESPONSE',
    })
  })

  it('does not block success:false envelopes even if schema is provided', async () => {
    ;(import.meta as any).env.VITE_API_BASE_URL = ''

    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ success: false, error: { code: 'NOPE', message: 'Nope' } }), {
          status: 200,
          statusText: 'OK',
          headers: { 'Content-Type': 'application/json' },
        }),
      ) as any,
    )

    const env = await requestJson('/api/v1/health', { schema: z.object({ n: z.number() }), toast: false })
    expect(env.success).toBe(false)
  })
})
