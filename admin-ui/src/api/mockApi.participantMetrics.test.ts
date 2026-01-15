import { afterEach, describe, expect, it, vi } from 'vitest'

import { mockApi } from './mockApi'

function jsonResponse(obj: unknown): Response {
  return new Response(JSON.stringify(obj), { status: 200, statusText: 'OK', headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('mockApi.participantMetrics', () => {
  it('computes balance_rows/counterparty/capacity for a participant', async () => {
    // Make scenario deterministic.
    const url = new URL('http://localhost/?scenario=happy')
    vi.stubGlobal('window', { ...window, location: url } as any)

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
        limit: '100.00',
        used: '25.00',
        available: '75.00',
        status: 'active',
        created_at: new Date().toISOString(),
      },
    ]

    const debts = [{ equivalent: 'GEO', debtor: 'PID_A', creditor: 'PID_B', amount: '10.00' }]

    const scenario = { name: 'happy', latency_ms: { min: 0, max: 0 } }

    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const u = String(input)
        if (u.includes('/admin-fixtures/v1/scenarios/happy.json')) return jsonResponse(scenario)
        if (u.includes('/admin-fixtures/v1/datasets/participants.json')) return jsonResponse(participants)
        if (u.includes('/admin-fixtures/v1/datasets/equivalents.json')) return jsonResponse(equivalents)
        if (u.includes('/admin-fixtures/v1/datasets/trustlines.json')) return jsonResponse(trustlines)
        if (u.includes('/admin-fixtures/v1/datasets/debts.json')) return jsonResponse(debts)
        // Optional datasets not needed for this test.
        return new Response('Not Found', { status: 404, statusText: 'Not Found' })
      }) as any,
    )

    const env = await mockApi.participantMetrics('PID_A', { equivalent: 'GEO', threshold: '0.10' })
    expect(env.success).toBe(true)
    if (!env.success) return

    expect(env.data.pid).toBe('PID_A')
    expect(env.data.equivalent).toBe('GEO')

    // balance_rows should include totals for GEO
    expect(env.data.balance_rows.length).toBe(1)
    expect(env.data.balance_rows[0]?.equivalent).toBe('GEO')
    expect(env.data.balance_rows[0]?.outgoing_limit).toBe('100.00')
    expect(env.data.balance_rows[0]?.outgoing_used).toBe('25.00')
    expect(env.data.balance_rows[0]?.total_debt).toBe('10.00')

    // counterparty split should reflect the debt to PID_B
    expect(env.data.counterparty?.eq).toBe('GEO')
    expect(env.data.counterparty?.creditors[0]?.pid).toBe('PID_B')

    // capacity should compute pct as ratio (used/limit)
    expect(env.data.capacity?.eq).toBe('GEO')
    expect(env.data.capacity?.out.pct).toBeCloseTo(0.25, 6)
  })
})
