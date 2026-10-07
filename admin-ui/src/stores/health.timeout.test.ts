import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { HEALTH_REQUEST_TIMEOUT_MS } from '../constants/timing'
import { useHealthStore } from './health'

// 032 S6 (E-2): through the real client, not a mock of it. A probe that never answers used to keep
// `_refreshPromise` alive for ever: every later `refresh()` returned that same promise, the status stayed on
// whatever it was, and no new request left the browser.

beforeEach(() => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  const meta = import.meta as unknown as { env: Record<string, unknown> }
  meta.env.VITE_API_BASE_URL = ''
  meta.env.VITE_ADMIN_TOKEN = 'test-token'
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('health store against a hub that stops answering (032 S6, E-2)', () => {
  it('settles with an error after the health bound and asks again on the next refresh', async () => {
    let hung = true
    const fetchMock = vi.fn((_url: unknown) =>
      hung
        ? new Promise<Response>(() => {})
        : Promise.resolve(new Response(JSON.stringify({ status: 'ok' }), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
    const store = useHealthStore()

    const first = store.refresh()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(HEALTH_REQUEST_TIMEOUT_MS)
    await first

    expect(store.loading).toBe(false)
    expect(store.error).toMatch(/timeout/i)
    expect(store.status).toBe('error')

    hung = false
    const second = store.refresh()
    await vi.advanceTimersByTimeAsync(0)
    await second
    // A new request went out: the store did not hand back the dead promise of the first one.
    expect(fetchMock.mock.calls.length).toBeGreaterThan(1)
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain('/api/v1/health')
  })
})
