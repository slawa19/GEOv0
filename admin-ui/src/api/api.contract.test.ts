import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ClearingCycles, GraphSnapshot } from '../types/domain'
import { mockApi, __resetMockApiForTests } from './mockApi'
import { realApi } from './realApi'

function jsonResponse(obj: unknown): Response {
  return new Response(JSON.stringify(obj), { status: 200, statusText: 'OK', headers: { 'Content-Type': 'application/json' } })
}

function assertGraphSnapshotShape(s: GraphSnapshot) {
  expect(Array.isArray(s.participants)).toBe(true)
  expect(Array.isArray(s.trustlines)).toBe(true)
  expect(Array.isArray(s.incidents)).toBe(true)
  expect(Array.isArray(s.equivalents)).toBe(true)
  expect(Array.isArray(s.debts)).toBe(true)
  expect(Array.isArray(s.audit_log)).toBe(true)
  expect(Array.isArray(s.transactions)).toBe(true)

  for (const p of s.participants) {
    expect(typeof p.pid).toBe('string')
    expect(typeof p.display_name).toBe('string')
    expect(typeof p.type).toBe('string')
    expect(typeof p.status).toBe('string')
  }
  for (const t of s.trustlines) {
    expect(typeof t.from).toBe('string')
    expect(typeof t.to).toBe('string')
    expect(typeof t.equivalent).toBe('string')
    expect(typeof t.status).toBe('string')
    expect(typeof t.created_at).toBe('string')
    expect(typeof t.limit).toBe('string')
    expect(typeof t.used).toBe('string')
    expect(typeof t.available).toBe('string')
  }
  for (const d of s.debts) {
    expect(typeof d.equivalent).toBe('string')
    expect(typeof d.debtor).toBe('string')
    expect(typeof d.creditor).toBe('string')
    expect(typeof d.amount).toBe('string')
  }
}

function assertClearingCyclesShape(c: ClearingCycles) {
  expect(c && typeof c === 'object').toBe(true)
  expect(c.equivalents && typeof c.equivalents === 'object').toBe(true)

  for (const [eq, v] of Object.entries(c.equivalents || {})) {
    expect(typeof eq).toBe('string')
    expect(v && typeof v === 'object').toBe(true)
    expect(Array.isArray(v.cycles)).toBe(true)

    for (const cycle of v.cycles || []) {
      expect(Array.isArray(cycle)).toBe(true)
      for (const edge of cycle || []) {
        expect(typeof edge.equivalent).toBe('string')
        expect(typeof edge.debtor).toBe('string')
        expect(typeof edge.creditor).toBe('string')
        expect(typeof edge.amount).toBe('string')
      }
    }
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  __resetMockApiForTests()
})

describe('API contract invariants', () => {
  it('mockApi.graphSnapshot and mockApi.graphEgo return GraphSnapshot-like shapes', async () => {
    const url = new URL('http://localhost/?scenario=happy')
    vi.stubGlobal('window', { ...window, location: url } as unknown as Window)

    const scenario = { name: 'happy', latency_ms: { min: 0, max: 0 } }

    const participants = [
      { pid: 'PID_A', display_name: 'Alice', type: 'person', status: 'active' },
      { pid: 'PID_B', display_name: 'Bob', type: 'person', status: 'active' },
    ]
    const equivalents = [{ code: 'GEO', precision: 2, description: 'GEO', is_active: true }]
    const trustlines = [
      {
        equivalent: 'GEO',
        from: 'PID_A',
        to: 'PID_B',
        from_display_name: 'Alice',
        to_display_name: 'Bob',
        limit: '100.00',
        used: '0.00',
        available: '100.00',
        status: 'active',
        created_at: new Date().toISOString(),
        policy: {},
      },
    ]

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const u = String(input)
        if (u.includes('/admin-fixtures/v1/scenarios/happy.json')) return jsonResponse(scenario)
        if (u.includes('/admin-fixtures/v1/datasets/participants.json')) return jsonResponse(participants)
        if (u.includes('/admin-fixtures/v1/datasets/equivalents.json')) return jsonResponse(equivalents)
        if (u.includes('/admin-fixtures/v1/datasets/trustlines.json')) return jsonResponse(trustlines)
        if (u.includes('/admin-fixtures/v1/datasets/incidents.json')) return jsonResponse({ items: [] })
        // Optional datasets:
        if (u.includes('/admin-fixtures/v1/datasets/debts.json')) return jsonResponse([])
        if (u.includes('/admin-fixtures/v1/datasets/audit-log.json')) return jsonResponse([])
        if (u.includes('/admin-fixtures/v1/datasets/transactions.json')) return jsonResponse([])
        return new Response('Not Found', { status: 404, statusText: 'Not Found' })
      })

    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    const snapEnv = await mockApi.graphSnapshot()
    expect(snapEnv.success).toBe(true)
    if (snapEnv.success) assertGraphSnapshotShape(snapEnv.data)

    const egoEnv = await mockApi.graphEgo({ pid: 'PID_A', depth: 1, equivalent: 'GEO', status: ['active'] })
    expect(egoEnv.success).toBe(true)
    if (egoEnv.success) assertGraphSnapshotShape(egoEnv.data)
  })

  it('realApi.graphSnapshot returns GraphSnapshot-like shape (envelope stub)', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    meta.env.PROD = false
    meta.env.DEV = true

    const payload: GraphSnapshot = {
      participants: [{ pid: 'PID_A', display_name: 'Alice', type: 'person', status: 'active' }],
      trustlines: [],
      incidents: [],
      equivalents: [{ code: 'GEO', precision: 2, description: 'GEO', is_active: true }],
      debts: [],
      audit_log: [],
      transactions: [],
    }

    const fetchMock = vi.fn(async () => jsonResponse({ success: true, data: payload }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    const env = await realApi.graphSnapshot()
    expect(env.success).toBe(true)
    if (env.success) assertGraphSnapshotShape(env.data)
  })

  it('realApi.graphSnapshot coerces decimal-like numbers to strings', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    meta.env.PROD = false
    meta.env.DEV = true

    const payload = {
      participants: [{ pid: 'PID_A', display_name: 'Alice', type: 'person', status: 'active' }],
      trustlines: [
        {
          equivalent: 'GEO',
          from: 'PID_A',
          to: 'PID_B',
          from_display_name: 'Alice',
          to_display_name: 'Bob',
          limit: 100.25,
          used: 0,
          available: 100.25,
          status: 'active',
          created_at: new Date().toISOString(),
          policy: {},
        },
      ],
      incidents: [],
      equivalents: [{ code: 'GEO', precision: 2, description: 'GEO', is_active: true }],
      debts: [{ equivalent: 'GEO', debtor: 'PID_B', creditor: 'PID_A', amount: 12.5 }],
      audit_log: [],
      transactions: [],
    } as unknown as GraphSnapshot

    const fetchMock = vi.fn(async () => jsonResponse({ success: true, data: payload }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    const env = await realApi.graphSnapshot()
    expect(env.success).toBe(true)
    if (!env.success) return

    expect(typeof env.data.trustlines[0]?.limit).toBe('string')
    expect(typeof env.data.trustlines[0]?.used).toBe('string')
    expect(typeof env.data.trustlines[0]?.available).toBe('string')
    expect(typeof env.data.debts[0]?.amount).toBe('string')
  })

  it('realApi.graphSnapshot rejects invalid payload shapes (schema drift guard)', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    meta.env.PROD = false
    meta.env.DEV = true

    const badPayload = {
      participants: [{ pid: 123, display_name: 'Alice', type: 'person', status: 'active' }],
      trustlines: [],
      incidents: [],
      equivalents: [{ code: 'GEO', precision: 2, description: 'GEO', is_active: true }],
      debts: [],
      audit_log: [],
      transactions: [],
    }

    const fetchMock = vi.fn(async () => jsonResponse({ success: true, data: badPayload }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    await expect(realApi.graphSnapshot()).rejects.toBeInstanceOf(Error)
  })

  it('realApi.clearingCycles returns ClearingCycles-like shape and coerces decimals', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    meta.env.PROD = false
    meta.env.DEV = true

    const payload = {
      equivalents: {
        GEO: {
          cycles: [
            [
              { equivalent: 'GEO', debtor: 'PID_B', creditor: 'PID_A', amount: 1.5 },
              { equivalent: 'GEO', debtor: 'PID_C', creditor: 'PID_B', amount: 2 },
            ],
          ],
        },
      },
    }

    const fetchMock = vi.fn(async () => jsonResponse({ success: true, data: payload }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    const env = await realApi.clearingCycles()
    expect(env.success).toBe(true)
    if (!env.success) return

    assertClearingCyclesShape(env.data)
  })

  it('realApi.clearingCycles rejects invalid payload shapes (schema drift guard)', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    meta.env.PROD = false
    meta.env.DEV = true

    const badPayload = {
      equivalents: {
        GEO: {
          cycles: [{ not: 'a-cycle' }],
        },
      },
    }

    const fetchMock = vi.fn(async () => jsonResponse({ success: true, data: badPayload }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    await expect(realApi.clearingCycles()).rejects.toBeInstanceOf(Error)
  })
})
