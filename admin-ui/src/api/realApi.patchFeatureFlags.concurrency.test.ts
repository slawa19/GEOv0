import { afterEach, describe, expect, it, vi } from 'vitest'

import { realApi } from './realApi'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('realApi.patchFeatureFlags concurrency', () => {
  it('sends independent partial patches without overwriting unrelated server state', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    meta.env.VITE_ADMIN_TOKEN = 'test-token'

    let flags: Record<string, unknown> = { multipath_enabled: false, full_multipath_enabled: false, clearing_enabled: false }
    const events: Array<{ method: string; body?: unknown }> = []

    let injectedServerUpdate = false
    const pendingResponses: Array<() => void> = []
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
      return new Promise<Response>((resolve) => {
        const method = (init?.method || 'GET').toUpperCase()

        if (method === 'PATCH') {
          const body = init?.body ? JSON.parse(String(init.body)) : {}
          events.push({ method: 'PATCH', body })

          // Simulate an independent server-side update immediately before the
          // first client patch is applied. A stale full-state body would revert it.
          if (!injectedServerUpdate) {
            flags = { ...flags, full_multipath_enabled: true }
            injectedServerUpdate = true
          }
          flags = { ...flags, ...(body as Record<string, unknown>) }

          pendingResponses.push(() =>
            resolve(
              new Response(JSON.stringify({ success: true, data: flags }), {
                status: 200,
                statusText: 'OK',
                headers: { 'Content-Type': 'application/json' },
              }),
            ),
          )
          return
        }

        pendingResponses.push(() => resolve(new Response('Unsupported', { status: 500, statusText: 'ERR' })))
      })
    })

    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    const inFlight = [
      realApi.patchFeatureFlags({ multipath_enabled: true }),
      realApi.patchFeatureFlags({ clearing_enabled: true }),
    ]

    expect(fetchMock.mock.calls.map(([, init]) => (init?.method || 'GET').toUpperCase())).toEqual(['PATCH', 'PATCH'])
    expect(events).toEqual([
      { method: 'PATCH', body: { multipath_enabled: true } },
      { method: 'PATCH', body: { clearing_enabled: true } },
    ])
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(flags).toEqual({ multipath_enabled: true, full_multipath_enabled: true, clearing_enabled: true })

    for (const respond of pendingResponses) respond()
    await Promise.all(inFlight)
  })
})
