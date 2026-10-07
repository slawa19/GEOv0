import { afterEach, describe, expect, it, vi } from 'vitest'

import { buildQuery, realApi } from './realApi'

function setBase(value: string) {
  const meta = import.meta as unknown as { env: Record<string, unknown> }
  meta.env.VITE_API_BASE_URL = value
}

afterEach(() => {
  vi.unstubAllGlobals()
  setBase('')
})

describe('realApi.buildQuery', () => {
  it('builds a relative URL when VITE_API_BASE_URL is empty (vite proxy scenario)', () => {
    setBase('')

    const url = buildQuery('/api/v1/admin/graph/ego', {
      pid: 'PID_ABC_DEF',
      depth: 2,
      equivalent: 'GEO',
      status: ['active', 'frozen'],
      q: '',
      unused: null,
    })

    expect(url.startsWith('/api/v1/admin/graph/ego')).toBe(true)

    const [, qs = ''] = url.split('?', 2)
    const sp = new URLSearchParams(qs)

    expect(sp.get('pid')).toBe('PID_ABC_DEF')
    expect(sp.get('depth')).toBe('2')
    expect(sp.get('equivalent')).toBe('GEO')
    expect(sp.getAll('status')).toEqual(['active', 'frozen'])

    // Ensure empty/null values are skipped.
    expect(sp.has('q')).toBe(false)
    expect(sp.has('unused')).toBe(false)
  })

  it('keeps the query of the path it is given and merges the parameters into it', () => {
    setBase('')
    expect(buildQuery('/api/v1/x?a=1', { b: 2 })).toBe('/api/v1/x?a=1&b=2')
    expect(buildQuery('/api/v1/x', {})).toBe('/api/v1/x')
  })
})

// 032 S6 (E-4): `buildQuery` used to return the base URL's own path (`/prefix/api/v1/...`) and `requestJson` then
// put the base in front of it again - every list request went to `/prefix/prefix/api/v1/...`.
describe('a base URL with a path prefix (032 S6, E-4)', () => {
  it('leaves the base to requestJson: buildQuery returns the path relative to it', () => {
    setBase('https://h.example/prefix')
    expect(buildQuery('/api/v1/admin/participants', { page: 1 })).toBe('/api/v1/admin/participants?page=1')
  })

  it.each([
    ['listParticipants', () => realApi.listParticipants({}), '/api/v1/admin/participants?'],
    ['listTrustlines', () => realApi.listTrustlines({}), '/api/v1/admin/trustlines?'],
    ['listAuditLog', () => realApi.listAuditLog({}), '/api/v1/admin/audit-log?'],
    ['listEquivalents', () => realApi.listEquivalents({ include_inactive: true }), '/api/v1/admin/equivalents?'],
  ])('%s sends one prefix', async (_name, call, tail) => {
    setBase('https://h.example/prefix')
    const fetchMock = vi.fn(async (_url: unknown) =>
      new Response(JSON.stringify({ items: [], page: 1, per_page: 20, total: 0 }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
    // The admin token is not under test; the dev default is seeded without a build flag.
    await call()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const sent = String(fetchMock.mock.calls[0]?.[0])
    expect(sent.startsWith(`https://h.example/prefix${tail}`)).toBe(true)
    expect(sent).not.toContain('/prefix/prefix')
  })
})
