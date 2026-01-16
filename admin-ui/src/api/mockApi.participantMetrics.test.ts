import { afterEach, describe, expect, it, vi } from 'vitest'

import { mockApi, __resetMockApiForTests } from './mockApi'

function jsonResponse(obj: unknown): Response {
  return new Response(JSON.stringify(obj), { status: 200, statusText: 'OK', headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => {
  vi.unstubAllGlobals()
  __resetMockApiForTests()
})

describe('mockApi.participantMetrics', () => {
  it('computes balance_rows/counterparty/capacity for a participant', async () => {
    // Make scenario deterministic.
    const url = new URL('http://localhost/?scenario=happy')
    vi.stubGlobal('window', { ...window, location: url } as unknown as Window)

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

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const u = String(input)
        if (u.includes('/admin-fixtures/v1/scenarios/happy.json')) return jsonResponse(scenario)
        if (u.includes('/admin-fixtures/v1/datasets/participants.json')) return jsonResponse(participants)
        if (u.includes('/admin-fixtures/v1/datasets/equivalents.json')) return jsonResponse(equivalents)
        if (u.includes('/admin-fixtures/v1/datasets/trustlines.json')) return jsonResponse(trustlines)
        if (u.includes('/admin-fixtures/v1/datasets/debts.json')) return jsonResponse(debts)
        // Optional datasets not needed for this test.
        return new Response('Not Found', { status: 404, statusText: 'Not Found' })
      })
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

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
  }, 15000)

  it('computes activity counters from trustlines/incidents/transactions', async () => {
    const url = new URL('http://localhost/?scenario=happy')
    vi.stubGlobal('window', { ...window, location: url } as unknown as Window)

    const participants = [
      { pid: 'PID_A', display_name: 'Alice', type: 'person', status: 'active' },
      { pid: 'PID_B', display_name: 'Bob', type: 'person', status: 'active' },
    ]

    const equivalents = [{ code: 'GEO', precision: 2, description: 'GEO', is_active: true }]

    const now = Date.now()
    const isoRecent = new Date(now - 2 * 24 * 3600 * 1000).toISOString()

    const trustlines = [
      {
        equivalent: 'GEO',
        from: 'PID_A',
        to: 'PID_B',
        limit: '100.00',
        used: '25.00',
        available: '75.00',
        status: 'active',
        created_at: isoRecent,
      },
      {
        equivalent: 'GEO',
        from: 'PID_B',
        to: 'PID_A',
        limit: '50.00',
        used: '0.00',
        available: '50.00',
        status: 'closed',
        created_at: isoRecent,
      },
    ]

    const debts: Array<{ equivalent: string; debtor: string; creditor: string; amount: string }> = []
    const incidents = [{ tx_id: 'TX1', state: 'open', initiator_pid: 'PID_A', equivalent: 'GEO', age_seconds: 60, sla_seconds: 0 }]
    const transactions = [
      {
        tx_id: 'T1',
        type: 'PAYMENT',
        initiator_pid: 'PID_A',
        payload: { equivalent: 'GEO' },
        state: 'COMMITTED',
        created_at: isoRecent,
        updated_at: isoRecent,
      },
      {
        tx_id: 'T2',
        type: 'CLEARING',
        initiator_pid: 'PID_A',
        payload: { equivalent: 'GEO' },
        state: 'ABORTED',
        created_at: isoRecent,
        updated_at: isoRecent,
      },
    ]

    const scenario = { name: 'happy', latency_ms: { min: 0, max: 0 } }

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const u = String(input)
        if (u.includes('/admin-fixtures/v1/scenarios/happy.json')) return jsonResponse(scenario)
        if (u.includes('/admin-fixtures/v1/datasets/participants.json')) return jsonResponse(participants)
        if (u.includes('/admin-fixtures/v1/datasets/equivalents.json')) return jsonResponse(equivalents)
        if (u.includes('/admin-fixtures/v1/datasets/trustlines.json')) return jsonResponse(trustlines)
        if (u.includes('/admin-fixtures/v1/datasets/debts.json')) return jsonResponse(debts)
        if (u.includes('/admin-fixtures/v1/datasets/incidents.json')) return jsonResponse(incidents)
        if (u.includes('/admin-fixtures/v1/datasets/transactions.json')) return jsonResponse(transactions)
        return new Response('Not Found', { status: 404, statusText: 'Not Found' })
      })
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    const env = await mockApi.participantMetrics('PID_A', { equivalent: 'GEO' })
    expect(env.success).toBe(true)
    if (!env.success) return

    expect(env.data.activity?.windows).toEqual([7, 30, 90])
    expect(env.data.activity?.trustline_created[7]).toBe(2)
    expect(env.data.activity?.trustline_closed[7]).toBe(1)
    expect(env.data.activity?.incident_count[7]).toBe(1)
    expect(env.data.activity?.participant_ops[7]).toBe(2)
    expect(env.data.activity?.payment_committed[7]).toBe(1)
    expect(env.data.activity?.clearing_committed[7]).toBe(0)
    expect(env.data.activity?.has_transactions).toBe(true)
  })
})
