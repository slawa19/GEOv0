// 028 E8 (`T2883`, F-028-49): the dev mock repeats the server contract of E5/E7.
import { afterEach, describe, expect, it, vi } from 'vitest'

import { __resetMockApiForTests, mockApi } from './mockApi'

const json = (obj: unknown) => new Response(JSON.stringify(obj), { status: 200, headers: { 'Content-Type': 'application/json' } })

function serve(datasets: Record<string, unknown>) {
  vi.stubGlobal('window', { ...window, location: new URL('http://localhost/?scenario=happy') } as unknown as Window)
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const u = String(input)
    if (u.includes('/scenarios/happy.json')) return json({ name: 'happy', latency_ms: { min: 0, max: 0 } })
    for (const [name, body] of Object.entries(datasets)) if (u.includes(`/datasets/${name}.json`)) return json(body)
    return new Response('Not Found', { status: 404 })
  }) as unknown as typeof fetch)
}

afterEach(() => {
  vi.unstubAllGlobals()
  __resetMockApiForTests()
})

const tl = (eq: string, limit: string, available: string, from: string) =>
  ({ equivalent: eq, from, to: 'PID_Z', limit, used: '0', available, status: 'active', created_at: '2026-01-01T00:00:00Z' })

describe('F-028-49: mock bottlenecks under ALL are ordered by the unitless share, like the server', () => {
  it('6 UAH of 600 (1%) is narrower than 5 HOUR of 10 (50%), although 5 < 6', async () => {
    serve({ trustlines: [tl('UAH', '600', '6', 'PID_U'), tl('HOUR', '10', '5', 'PID_H')], participants: [],
      debts: [], incidents: { items: [] } })
    const env = await mockApi.trustlineBottlenecks({ threshold: '0.60' })
    expect(env.success && env.data.items.map((t) => t.from)).toEqual(['PID_U', 'PID_H'])
    const summary = await mockApi.liquiditySummary({ threshold: '0.60' })
    expect(summary.success && summary.data.top_bottleneck_edges.map((t) => t.from)).toEqual(['PID_U', 'PID_H'])
    // Counter-check: inside one equivalent the order stays by the amount (server `available_expr.asc()`).
    const one = await mockApi.trustlineBottlenecks({ threshold: '0.60', equivalent: 'UAH' })
    expect(one.success && one.data.items.map((t) => t.from)).toEqual(['PID_U'])
  })
})

describe('F-028-49: mock activity attributes committed transactions by their parties, not by the initiator', () => {
  it('counts a payment by from/to and a clearing by its edges', async () => {
    const now = new Date().toISOString()
    const tx = (type: string, payload: Record<string, unknown>, initiator: string | null) =>
      ({ tx_id: `${type}-${initiator}`, type, state: 'COMMITTED', initiator_pid: initiator, payload, created_at: now, updated_at: now })
    serve({
      participants: [{ pid: 'PID_A', display_name: 'A', type: 'person', status: 'active' }],
      equivalents: [{ code: 'UAH', precision: 2, description: '', is_active: true }],
      trustlines: [], debts: [], incidents: [],
      transactions: [
        tx('PAYMENT', { from: 'PID_X', to: 'PID_A', equivalent: 'UAH' }, 'PID_X'),
        tx('CLEARING', { equivalent: 'UAH', edges: [{ debtor: 'PID_A', creditor: 'PID_B' }] }, null),
        tx('PAYMENT', { from: 'PID_X', to: 'PID_Y', equivalent: 'UAH' }, 'PID_A'),
      ],
    })
    const env = await mockApi.participantMetrics('PID_A', { equivalent: 'UAH' })
    expect(env.success).toBe(true)
    if (!env.success) return
    expect(env.data.activity.payment_committed[7]).toBe(1)
    expect(env.data.activity.clearing_committed[7]).toBe(1)
  })
})
