import { afterEach, describe, expect, it, vi } from 'vitest'

import type { GraphSnapshot } from '../types/domain'
import { realApi } from './realApi'

function jsonResponse(obj: unknown): Response {
  return new Response(JSON.stringify(obj), { status: 200, statusText: 'OK', headers: { 'Content-Type': 'application/json' } })
}

function assertGraphSnapshotShape(s: GraphSnapshot) {
  expect(Array.isArray(s.participants)).toBe(true)
  expect(Array.isArray(s.trustlines)).toBe(true)
  // 032 S5 (A-4): the snapshot carries no incidents collection.
  expect('incidents' in s).toBe(false)
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

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('API contract invariants', () => {
  it('realApi.graphSnapshot returns GraphSnapshot-like shape (raw payload)', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    meta.env.PROD = false
    meta.env.DEV = true

    const payload: GraphSnapshot = {
      participants: [{ pid: 'PID_A', display_name: 'Alice', type: 'person', status: 'active' }],
      trustlines: [],
      equivalents: [{ code: 'GEO', precision: 2, description: 'GEO', is_active: true }],
      debts: [],
      audit_log: [],
      // 028 F-028-45: a clearing records no initiator - the decoder must take `initiator_pid: null`.
      transactions: [{ tx_id: 'cl-1', type: 'CLEARING', state: 'COMMITTED', initiator_pid: null, equivalent: 'GEO',
        created_at: '2026-10-04T00:00:00Z', updated_at: '2026-10-04T00:00:00Z', edges: [{ debtor: 'PID_A', creditor: 'PID_B' }] }],
    }

    const fetchMock = vi.fn(async () => jsonResponse(payload))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    assertGraphSnapshotShape(await realApi.graphSnapshot())
  })

  // 032 S6 (E-6): a money field is a decimal STRING on the wire (`PlainDecimal`). This test used to pin the opposite -
  // that a JSON number was accepted and turned into a string - which let a float reach a money cell: `100.25` is
  // not exactly representable, and `String(0.1 + 0.2)` is `0.30000000000000004`. A number there is now a schema
  // violation, shown as INVALID_RESPONSE, exactly like any other drift.
  const graphWithMoney = (money: { limit: unknown; used: unknown; available: unknown; amount: unknown }) => ({
    participants: [{ pid: 'PID_A', display_name: 'Alice', type: 'person', status: 'active' }],
    trustlines: [
      {
        equivalent: 'GEO',
        from: 'PID_A',
        to: 'PID_B',
        from_display_name: 'Alice',
        to_display_name: 'Bob',
        limit: money.limit,
        used: money.used,
        available: money.available,
        status: 'active',
        created_at: new Date().toISOString(),
        policy: {},
      },
    ],
    equivalents: [{ code: 'GEO', precision: 2, description: 'GEO', is_active: true }],
    debts: [{ equivalent: 'GEO', debtor: 'PID_B', creditor: 'PID_A', amount: money.amount }],
    audit_log: [],
    transactions: [],
  })

  it('realApi.graphSnapshot keeps decimal strings exactly as sent', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    meta.env.PROD = false
    meta.env.DEV = true

    const fetchMock = vi.fn(async () =>
      jsonResponse(graphWithMoney({ limit: '100.25000000', used: '0', available: '100.25000000', amount: '12.50' })),
    )
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    const snapshot = await realApi.graphSnapshot()

    expect(snapshot.trustlines[0]?.limit).toBe('100.25000000')
    expect(snapshot.trustlines[0]?.used).toBe('0')
    expect(snapshot.trustlines[0]?.available).toBe('100.25000000')
    expect(snapshot.debts[0]?.amount).toBe('12.50')
  })

  it.each([
    ['trustline limit', { limit: 100.25, used: '0', available: '100.25', amount: '1' }],
    ['trustline used', { limit: '1', used: 0, available: '1', amount: '1' }],
    ['trustline available', { limit: '1', used: '0', available: 1, amount: '1' }],
    ['debt amount', { limit: '1', used: '0', available: '1', amount: 12.5 }],
  ])('realApi.graphSnapshot rejects a JSON number in the money field: %s', async (_field, money) => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    meta.env.PROD = false
    meta.env.DEV = true

    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(graphWithMoney(money))) as unknown as typeof fetch)

    await expect(realApi.graphSnapshot()).rejects.toMatchObject({ name: 'ApiException', code: 'INVALID_RESPONSE' })
  })

  it('realApi.liquiditySummary and participantMetrics reject a JSON number in a money field', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    meta.env.PROD = false
    meta.env.DEV = true

    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({ equivalent: 'GEO', updated_at: 'x', active_trustlines: 1, total_limit: 10, total_used: '0', total_available: '10' }),
      ) as unknown as typeof fetch,
    )
    await expect(realApi.liquiditySummary({ equivalent: 'GEO' })).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })

    const row = {
      equivalent: 'GEO',
      outgoing_limit: '1',
      outgoing_used: '0',
      incoming_limit: '1',
      incoming_used: '0',
      total_debt: 0,
      total_credit: '0',
      net: '0',
    }
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ pid: 'PID_A', equivalent: 'GEO', balance_rows: [row] })) as unknown as typeof fetch,
    )
    await expect(realApi.participantMetrics('PID_A', { equivalent: 'GEO' })).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })

  // 032 S6 (E-5): the server sends `description: null` for an equivalent without one (`StoredEquivalent` is
  // nullable) while the UI type says `description: string`. The reads now say what the type says.
  it('realApi.listEquivalents and the graph give an equivalent without a description an empty one, as the type says', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    meta.env.PROD = false
    meta.env.DEV = true

    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({ items: [{ code: 'GEO', precision: 2, description: null, is_active: true }, { code: 'EUR', precision: 2, is_active: true }] }),
      ) as unknown as typeof fetch,
    )
    const list = await realApi.listEquivalents({})
    expect(list.items.map((e) => e.description)).toEqual(['', ''])

    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          ...graphWithMoney({ limit: '1', used: '0', available: '1', amount: '1' }),
          equivalents: [{ code: 'GEO', precision: 2, description: null, is_active: true }],
        }),
      ) as unknown as typeof fetch,
    )
    const snapshot = await realApi.graphSnapshot()
    expect(snapshot.equivalents[0]?.description).toBe('')
  })

  it('realApi.graphSnapshot rejects invalid payload shapes (schema drift guard)', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    meta.env.PROD = false
    meta.env.DEV = true

    const badPayload = {
      participants: [{ pid: 123, display_name: 'Alice', type: 'person', status: 'active' }],
      trustlines: [],
      equivalents: [{ code: 'GEO', precision: 2, description: 'GEO', is_active: true }],
      debts: [],
      audit_log: [],
      transactions: [],
    }

    const fetchMock = vi.fn(async () => jsonResponse(badPayload))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    await expect(realApi.graphSnapshot()).rejects.toBeInstanceOf(Error)
  })

})
