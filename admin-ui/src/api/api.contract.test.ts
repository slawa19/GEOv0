import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ClearingCycles, GraphSnapshot } from '../types/domain'
import { realApi } from './realApi'

// The schema-drift tests below make realApi reject on purpose, and a rejection calls toastApiError, which mounts a real
// ElMessage. Its transition runs on a later animation frame - inside whichever test is running by then; the test that
// stubs `window` with a spread copy has no getComputedStyle, and a shuffled order throws it as an unhandled error
// (029 F-029-15). The toast is not what these tests check, so it does not get to outlive them.
vi.mock('./errorToast', () => ({ toastApiError: vi.fn(async () => {}) }))

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
      incidents: [],
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

    const fetchMock = vi.fn(async () => jsonResponse(payload))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    const snapshot = await realApi.graphSnapshot()

    expect(typeof snapshot.trustlines[0]?.limit).toBe('string')
    expect(typeof snapshot.trustlines[0]?.used).toBe('string')
    expect(typeof snapshot.trustlines[0]?.available).toBe('string')
    expect(typeof snapshot.debts[0]?.amount).toBe('string')
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

    const fetchMock = vi.fn(async () => jsonResponse(badPayload))
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

    const fetchMock = vi.fn(async () => jsonResponse(payload))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    assertClearingCyclesShape(await realApi.clearingCycles())
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

    const fetchMock = vi.fn(async () => jsonResponse(badPayload))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    await expect(realApi.clearingCycles()).rejects.toBeInstanceOf(Error)
  })
})
