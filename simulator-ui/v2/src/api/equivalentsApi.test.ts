import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchEquivalentPrecisions } from './equivalentsApi'

/**
 * BACKLOG 037-5 (d): which catalogue endpoint the simulator UI asks, by the credential it holds.
 * `GET /equivalents` accepts a participant JWT only; `GET /admin/equivalents` accepts `X-Admin-Token`
 * (or, in a dev backend, a trusted address without a token). The test sees the requests the client makes,
 * not what a real backend answers to them.
 */

type Seen = { path: string; admin: string | null; bearer: string | null }

const CATALOGUE = { items: [{ code: 'UAH', precision: 2 }] }

function stubBackend(answer: (path: string) => { status: number; body?: unknown }): Seen[] {
  const seen: Seen[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const path = String(url).replace('http://x/api/v1', '')
    const headers = new Headers(init?.headers)
    seen.push({ path, admin: headers.get('X-Admin-Token'), bearer: headers.get('Authorization') })
    const { status, body } = answer(path)
    return new Response(body === undefined ? '' : JSON.stringify(body), { status })
  }))
  return seen
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('fetchEquivalentPrecisions: one request the held credential can pass', () => {
  it('an admin token (the dev default) asks the admin catalogue only - never the participant endpoint that answers it 401', async () => {
    const seen = stubBackend((path) => (path === '/admin/equivalents' ? { status: 200, body: CATALOGUE } : { status: 401 }))

    const rows = await fetchEquivalentPrecisions({ apiBase: 'http://x/api/v1', accessToken: 'dev-admin-token-change-me' })

    expect(rows).toEqual([{ code: 'UAH', precision: 2 }])
    expect(seen).toEqual([{ path: '/admin/equivalents', admin: 'dev-admin-token-change-me', bearer: null }])
  })

  it('no token: the admin catalogue is the only one that may answer (a dev backend trusts the address); one request', async () => {
    const seen = stubBackend((path) => (path === '/admin/equivalents' ? { status: 200, body: CATALOGUE } : { status: 401 }))

    const rows = await fetchEquivalentPrecisions({ apiBase: 'http://x/api/v1', accessToken: '' })

    expect(rows).toEqual([{ code: 'UAH', precision: 2 }])
    expect(seen.map((s) => s.path)).toEqual(['/admin/equivalents'])
  })

  it('anti-vacuum: a participant JWT still asks the canonical endpoint, and a refusal there is still retried on the admin one', async () => {
    const ok = stubBackend((path) => (path === '/equivalents' ? { status: 200, body: CATALOGUE } : { status: 403 }))
    expect(await fetchEquivalentPrecisions({ apiBase: 'http://x/api/v1', accessToken: 'a.b.c' })).toEqual([{ code: 'UAH', precision: 2 }])
    expect(ok).toEqual([{ path: '/equivalents', admin: null, bearer: 'Bearer a.b.c' }])

    const refused = stubBackend((path) => (path === '/equivalents' ? { status: 401 } : { status: 200, body: CATALOGUE }))
    expect(await fetchEquivalentPrecisions({ apiBase: 'http://x/api/v1', accessToken: 'a.b.c' })).toEqual([{ code: 'UAH', precision: 2 }])
    expect(refused.map((s) => s.path)).toEqual(['/equivalents', '/admin/equivalents'])
  })

  it('a refused request is still an error for the caller (who keeps the shipped precisions)', async () => {
    stubBackend(() => ({ status: 403 }))
    await expect(fetchEquivalentPrecisions({ apiBase: 'http://x/api/v1', accessToken: '' })).rejects.toMatchObject({ status: 403 })
  })
})
