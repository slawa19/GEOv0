import { afterEach, describe, expect, it, vi } from 'vitest'

import { realApi } from './realApi'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('realApi.patchFeatureFlags concurrency', () => {
  it('serializes concurrent patches to reduce lost updates', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    meta.env.VITE_ADMIN_TOKEN = 'test-token'

    let flags: Record<string, unknown> = { multipath_enabled: false, full_multipath_enabled: false, clearing_enabled: false }
    const events: Array<{ method: string; body?: unknown }> = []

    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      const method = (init?.method || 'GET').toUpperCase()

      if (method === 'GET') {
        events.push({ method: 'GET' })
        return new Response(JSON.stringify({ success: true, data: flags }), {
          status: 200,
          statusText: 'OK',
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (method === 'PATCH') {
        const body = init?.body ? JSON.parse(String(init.body)) : {}
        events.push({ method: 'PATCH', body })

        // Simulate server-side update and a bit of latency.
        flags = { ...flags, ...(body as Record<string, unknown>) }
        await new Promise((r) => setTimeout(r, 10))

        return new Response(JSON.stringify({ success: true, data: flags }), {
          status: 200,
          statusText: 'OK',
          headers: { 'Content-Type': 'application/json' },
        })
      }

      return new Response('Unsupported', { status: 500, statusText: 'ERR' })
    })

    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    await Promise.all([
      realApi.patchFeatureFlags({ multipath_enabled: true }),
      realApi.patchFeatureFlags({ clearing_enabled: true }),
    ])

    // Expect strict order due to client-side serialization.
    expect(events.map((e) => e.method)).toEqual(['GET', 'PATCH', 'GET', 'PATCH'])

    expect(events).toHaveLength(4)

    // Second PATCH should include the first patch result, avoiding local lost update.
    const secondPatch = events[3]!
    expect(secondPatch.method).toBe('PATCH')
    expect(secondPatch.body).toMatchObject({ multipath_enabled: true, clearing_enabled: true })

    expect(flags).toMatchObject({ multipath_enabled: true, clearing_enabled: true })
  })
})
