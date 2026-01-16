import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiException } from './envelope'
import { __resetMockApiForTests, mockApi } from './mockApi'

function jsonResponse(obj: unknown): Response {
  return new Response(JSON.stringify(obj), {
    status: 200,
    statusText: 'OK',
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
  __resetMockApiForTests()
})

describe('mockApi list endpoints', () => {
  it('listParticipants: supports filtering (q/status/type) and pagination', async () => {
    const url = new URL('http://localhost/?scenario=happy')
    vi.stubGlobal('window', { ...window, location: url } as unknown as Window)

    const scenario = { name: 'happy', latency_ms: { min: 0, max: 0 } }

    const participants = [
      { pid: 'p1', display_name: 'Alice', type: 'person', status: 'active' },
      { pid: 'p2', display_name: 'Bob', type: 'person', status: 'frozen' },
      { pid: 'p3', display_name: 'ACME Coop', type: 'org', status: 'active' },
      { pid: 'p4', display_name: 'Carol', type: 'person', status: 'active' },
    ]

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const u = String(input)
        if (u.includes('/admin-fixtures/v1/scenarios/happy.json')) return jsonResponse(scenario)
        if (u.includes('/admin-fixtures/v1/datasets/participants.json')) return jsonResponse(participants)
        return new Response('Not Found', { status: 404, statusText: 'Not Found' })
      })
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    const env = await mockApi.listParticipants({ q: 'a', status: 'active', page: 2, per_page: 1 })
    expect(env.success).toBe(true)
    if (!env.success) return

    // Matches: Alice, ACME Coop, Carol (3 total). Second page (per_page=1) => ACME Coop.
    expect(env.data.total).toBe(3)
    expect(env.data.page).toBe(2)
    expect(env.data.per_page).toBe(1)
    expect(env.data.items.map((p) => p.pid)).toEqual(['p3'])

    // Filtering by type should narrow further.
    const envType = await mockApi.listParticipants({ q: 'a', status: 'active', type: 'person', page: 1, per_page: 20 })
    expect(envType.success).toBe(true)
    if (envType.success) {
      expect(envType.data.items.map((p) => p.pid).sort()).toEqual(['p1', 'p4'])
    }
  })

  it('listTrustlines: supports filtering (equivalent/creditor/debtor/status) and pagination', async () => {
    const url = new URL('http://localhost/?scenario=happy')
    vi.stubGlobal('window', { ...window, location: url } as unknown as Window)

    const scenario = { name: 'happy', latency_ms: { min: 0, max: 0 } }

    const trustlines = [
      {
        equivalent: 'USD',
        from: 'p1',
        to: 'p2',
        limit: '10',
        used: '1',
        available: '9',
        status: 'active',
        created_at: '2026-01-01T00:00:00Z',
      },
      {
        equivalent: 'USD',
        from: 'p3',
        to: 'p2',
        limit: '20',
        used: '0',
        available: '20',
        status: 'active',
        created_at: '2026-01-01T00:00:00Z',
      },
      {
        equivalent: 'EUR',
        from: 'p1',
        to: 'p4',
        limit: '30',
        used: '5',
        available: '25',
        status: 'frozen',
        created_at: '2026-01-01T00:00:00Z',
      },
    ]

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const u = String(input)
        if (u.includes('/admin-fixtures/v1/scenarios/happy.json')) return jsonResponse(scenario)
        if (u.includes('/admin-fixtures/v1/datasets/trustlines.json')) return jsonResponse(trustlines)
        return new Response('Not Found', { status: 404, statusText: 'Not Found' })
      })
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    const env = await mockApi.listTrustlines({ equivalent: 'usd', creditor: 'p', debtor: 'p2', status: 'active', page: 1, per_page: 1 })
    expect(env.success).toBe(true)
    if (!env.success) return

    // Filter matches 2 USD active trustlines to p2. Page 1 per_page 1 returns first.
    expect(env.data.total).toBe(2)
    expect(env.data.items).toHaveLength(1)
    expect(env.data.items[0]!.equivalent).toBe('USD')
    expect(env.data.items[0]!.to).toBe('p2')

    const env2 = await mockApi.listTrustlines({ equivalent: 'USD', debtor: 'p2', status: 'active', page: 2, per_page: 1 })
    expect(env2.success).toBe(true)
    if (env2.success) {
      expect(env2.data.items).toHaveLength(1)
      expect(env2.data.items[0]!.from).toBe('p3')
    }
  })

  it('scenario override (empty) returns empty lists without loading datasets', async () => {
    const url = new URL('http://localhost/?scenario=empty')
    vi.stubGlobal('window', { ...window, location: url } as unknown as Window)

    const scenario = {
      name: 'empty',
      latency_ms: { min: 0, max: 0 },
      overrides: {
        '/api/v1/admin/participants': { mode: 'empty' },
      },
    }

    const fetchSpy = vi.fn(async (input: RequestInfo | URL) => {
      const u = String(input)
      if (u.includes('/admin-fixtures/v1/scenarios/empty.json')) return jsonResponse(scenario)
      if (u.includes('/admin-fixtures/v1/datasets/participants.json')) {
        return jsonResponse([{ pid: 'p1', display_name: 'Alice', type: 'person', status: 'active' }])
      }
      return new Response('Not Found', { status: 404, statusText: 'Not Found' })
    })

    vi.stubGlobal('fetch', fetchSpy as unknown as typeof fetch)

    const env = await mockApi.listParticipants({ page: 1, per_page: 20 })
    expect(env.success).toBe(true)
    if (env.success) {
      expect(env.data.items).toEqual([])
      expect(env.data.total).toBe(0)
    }

    const datasetCalls = fetchSpy.mock.calls.filter((c) => String(c[0]).includes('/datasets/participants.json'))
    expect(datasetCalls).toHaveLength(0)
  })

  it('scenario override (error) throws ApiException', async () => {
    const url = new URL('http://localhost/?scenario=boom')
    vi.stubGlobal('window', { ...window, location: url } as unknown as Window)

    const scenario = {
      name: 'boom',
      latency_ms: { min: 0, max: 0 },
      overrides: {
        '/api/v1/admin/trustlines': { mode: 'error', status: 500, code: 'INTERNAL_ERROR', message: 'boom' },
      },
    }

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const u = String(input)
        if (u.includes('/admin-fixtures/v1/scenarios/boom.json')) return jsonResponse(scenario)
        if (u.includes('/admin-fixtures/v1/datasets/trustlines.json')) {
          return jsonResponse([
            {
              equivalent: 'USD',
              from: 'p1',
              to: 'p2',
              limit: '10',
              used: '1',
              available: '9',
              status: 'active',
              created_at: '2026-01-01T00:00:00Z',
            },
          ])
        }
        return new Response('Not Found', { status: 404, statusText: 'Not Found' })
      })
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    await expect(mockApi.listTrustlines({})).rejects.toMatchObject({
      name: 'ApiException',
      status: 500,
      code: 'INTERNAL_ERROR',
      message: 'boom',
    } satisfies Partial<ApiException>)
  })
})
