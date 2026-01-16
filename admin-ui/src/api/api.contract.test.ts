import { afterEach, describe, expect, it, vi } from 'vitest'

import type { GraphSnapshot } from '../types/domain'
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
  }
  for (const t of s.trustlines) {
    expect(typeof t.from).toBe('string')
    expect(typeof t.to).toBe('string')
    expect(typeof t.equivalent).toBe('string')
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
})
