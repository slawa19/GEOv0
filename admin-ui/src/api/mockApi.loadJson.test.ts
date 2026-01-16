import { afterEach, describe, expect, it, vi } from 'vitest'

import { __resetMockApiForTests, mockApi } from './mockApi'

function jsonResponse(obj: unknown): Response {
  return new Response(JSON.stringify(obj), { status: 200, statusText: 'OK', headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
  __resetMockApiForTests()
})

describe('mockApi.loadJson via endpoints', () => {
  it('retries failed fixture fetches (3 attempts) and succeeds', async () => {
    vi.useFakeTimers()

    const url = new URL('http://localhost/?scenario=happy')
    vi.stubGlobal('window', { ...window, location: url } as unknown as Window)

    const scenario = { name: 'happy', latency_ms: { min: 0, max: 0 } }

    let healthCalls = 0

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const u = String(input)
        if (u.includes('/admin-fixtures/v1/scenarios/happy.json')) return jsonResponse(scenario)
        if (u.includes('/admin-fixtures/v1/datasets/health.json')) {
          healthCalls += 1
          if (healthCalls < 3) return new Response('boom', { status: 500, statusText: 'Server Error' })
          return jsonResponse({ ok: true })
        }
        return new Response('Not Found', { status: 404, statusText: 'Not Found' })
      })
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    const p = mockApi.health()

    // Run all pending timers (scenario sleep + retry backoffs)
    await vi.runAllTimersAsync()

    const env = await p
    expect(env.success).toBe(true)
    if (env.success) expect(env.data).toEqual({ ok: true })
    expect(healthCalls).toBe(3)
  })

  it('returns cached fixtures without refetch (offline-friendly)', async () => {
    const url = new URL('http://localhost/?scenario=happy')
    vi.stubGlobal('window', { ...window, location: url } as unknown as Window)

    const scenario = { name: 'happy', latency_ms: { min: 0, max: 0 } }

    let healthCalls = 0

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const u = String(input)
        if (u.includes('/admin-fixtures/v1/scenarios/happy.json')) return jsonResponse(scenario)
        if (u.includes('/admin-fixtures/v1/datasets/health.json')) {
          healthCalls += 1
          return jsonResponse({ ok: true })
        }
        return new Response('Not Found', { status: 404, statusText: 'Not Found' })
      })
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    const a = await mockApi.health()
    expect(a.success).toBe(true)

    // Even if fetch would fail now, cache should short-circuit.
    const fetchOfflineMock = vi.fn(async () => new Response('offline', { status: 0, statusText: 'Offline' }))
    vi.stubGlobal('fetch', fetchOfflineMock as unknown as typeof fetch)

    const b = await mockApi.health()
    expect(b.success).toBe(true)
    expect(healthCalls).toBe(1)
  })
})
