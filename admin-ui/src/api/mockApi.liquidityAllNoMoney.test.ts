// 028 closing (§19.5, finding 1, owner В-3): under ALL (no equivalent) the mock must not COMPUTE money
// aggregates or cross-equivalent nets, exactly like the server (`app/api/v1/admin.py`, F-028-37).
// Hiding the result afterwards (`null` / `[]`) is not enough: the arithmetic must never run.
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../utils/decimal', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../utils/decimal')>()
  return {
    ...actual,
    addDecimalStrings: vi.fn(actual.addDecimalStrings),
    compareDecimalStrings: vi.fn(actual.compareDecimalStrings),
    absDecimalString: vi.fn(actual.absDecimalString),
  }
})

import * as decimal from '../utils/decimal'
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
  vi.mocked(decimal.addDecimalStrings).mockClear()
  vi.mocked(decimal.compareDecimalStrings).mockClear()
  vi.mocked(decimal.absDecimalString).mockClear()
  __resetMockApiForTests()
})

const tl = (eq: string, limit: string, used: string, available: string, from: string) =>
  ({ equivalent: eq, from, to: 'PID_Z', limit, used, available, status: 'active', created_at: '2026-01-01T00:00:00Z' })
const debt = (eq: string, debtor: string, creditor: string, amount: string) => ({ equivalent: eq, debtor, creditor, amount })

const datasets = () => ({
  participants: [
    { pid: 'PID_U', display_name: 'U', type: 'person', status: 'active' },
    { pid: 'PID_H', display_name: 'H', type: 'person', status: 'active' },
  ],
  trustlines: [tl('UAH', '600', '594', '6', 'PID_U'), tl('HOUR', '10', '5', '5', 'PID_H')],
  debts: [debt('UAH', 'PID_U', 'PID_H', '594.00'), debt('HOUR', 'PID_H', 'PID_U', '5.00')],
  incidents: { items: [] },
})

describe('mock liquiditySummary under ALL: no money arithmetic at all', () => {
  it('returns counters and the unitless bottleneck list, and never calls the money helpers', async () => {
    serve(datasets())
    const env = await mockApi.liquiditySummary({ threshold: '0.60' })
    expect(env.success).toBe(true)
    if (!env.success) return
    expect(env.data.equivalent).toBeNull()
    expect(env.data.active_trustlines).toBe(2)
    expect(env.data.bottlenecks).toBe(2)
    expect(env.data.total_limit).toBeNull()
    expect(env.data.total_used).toBeNull()
    expect(env.data.total_available).toBeNull()
    expect(env.data.top_creditors).toEqual([])
    expect(env.data.top_debtors).toEqual([])
    expect(env.data.top_by_abs_net).toEqual([])
    expect(env.data.top_bottleneck_edges.map((t) => t.from)).toEqual(['PID_U', 'PID_H'])
    expect(decimal.addDecimalStrings).not.toHaveBeenCalled()
    expect(decimal.compareDecimalStrings).not.toHaveBeenCalled()
    expect(decimal.absDecimalString).not.toHaveBeenCalled()
  })

  it('positive control: with one equivalent the sums and nets are computed from that equivalent only', async () => {
    serve(datasets())
    const env = await mockApi.liquiditySummary({ threshold: '0.60', equivalent: 'UAH' })
    expect(env.success).toBe(true)
    if (!env.success) return
    expect(env.data.equivalent).toBe('UAH')
    expect(env.data.total_limit).toBe('600')
    expect(env.data.total_used).toBe('594')
    expect(env.data.total_available).toBe('6')
    expect(env.data.top_creditors.map((r) => r.pid)).toEqual(['PID_H'])
    expect(env.data.top_debtors.map((r) => r.pid)).toEqual(['PID_U'])
    expect(decimal.addDecimalStrings).toHaveBeenCalled()
  })
})
