import { afterEach, describe, expect, it, vi } from 'vitest'

import { __resetMockApiForTests, mockApi } from './mockApi'

/**
 * RT-013-6 -- `F-013-6`: the mock filtered trustlines by substring, production by exact equality.
 *
 * THE ORACLE IS PRODUCTION, NOT THE MOCK.  Every expectation below is derived from what the
 * backend answers for the same operator action, read on HEAD:
 *
 *   `GET /api/v1/admin/trustlines` (`app/api/v1/admin.py:1396-1424`) hands `creditor`/`debtor`/
 *   `equivalent` to `TrustLineService` verbatim -- no trim, no case folding.
 *   `TrustLineService.list_all` (`app/core/trustlines/service.py:618`) resolves each pid with
 *   `select(Participant.id).where(Participant.pid == creditor_pid)` and RETURNS `[]` when the
 *   pid does not resolve; `count_all` (`:675`) duplicates the same logic and returns `0`.
 *   `equivalent` resolves through `Equivalent.code == equivalent` (`:655`, `:711`), and an
 *   equivalent code is `^[A-Z0-9_]{1,16}$` (`app/utils/validation.py:9`), so a lower-cased code
 *   can never resolve.
 *   The admin-ui real client does not normalise either: `realApi.listTrustlines`
 *   (`admin-ui/src/api/realApi.ts:763-777`) spreads the params straight into `buildQuery`
 *   (`:559-583`), which only drops empty strings.
 *
 * So: a PARTIAL pid, a lower-cased code and a space-padded pid all return NOTHING in production.
 * The mock must agree, or the demo shows results the product never shows.
 *
 * WHAT THIS TEST CAN AND CANNOT TELL APART
 *  - CAN distinguish: substring match (`.includes`), prefix match, case-insensitive equality,
 *    a filter applied to the wrong field (the positive controls assert WHICH rows come back,
 *    not just how many), a filter dropped entirely, and a mock that trims its input.
 *  - CANNOT distinguish: pagination arithmetic (covered by
 *    `mockApi.listEndpoints.test.ts`), nor the backend's own behaviour -- production is quoted
 *    here as a contract, not executed.  It also says nothing about `status`, which already
 *    compares with `!==` on both sides.
 */

function jsonResponse(obj: unknown): Response {
  return new Response(JSON.stringify(obj), {
    status: 200,
    statusText: 'OK',
    headers: { 'Content-Type': 'application/json' },
  })
}

const TRUSTLINES = [
  { equivalent: 'USD', from: 'p1', to: 'p2', limit: '10', used: '1', available: '9', status: 'active', created_at: '2026-01-01T00:00:00Z' },
  { equivalent: 'USD', from: 'p3', to: 'p2', limit: '20', used: '0', available: '20', status: 'active', created_at: '2026-01-01T00:00:00Z' },
  { equivalent: 'EUR', from: 'p1', to: 'p4', limit: '30', used: '5', available: '25', status: 'frozen', created_at: '2026-01-01T00:00:00Z' },
]

function installFixtures() {
  const url = new URL('http://localhost/?scenario=happy')
  vi.stubGlobal('window', { ...window, location: url } as unknown as Window)

  const scenario = { name: 'happy', latency_ms: { min: 0, max: 0 } }
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const u = String(input)
    if (u.includes('/admin-fixtures/v1/scenarios/happy.json')) return jsonResponse(scenario)
    if (u.includes('/admin-fixtures/v1/datasets/trustlines.json')) return jsonResponse(TRUSTLINES)
    return new Response('Not Found', { status: 404, statusText: 'Not Found' })
  })
  vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
}

type Row = { equivalent: string; from: string; to: string }

async function listed(params: Parameters<typeof mockApi.listTrustlines>[0]): Promise<{ total: number; rows: Row[] }> {
  const env = await mockApi.listTrustlines({ page: 1, per_page: 50, ...params })
  expect(env.success).toBe(true)
  if (!env.success) throw new Error('unreachable')
  return {
    total: env.data.total,
    rows: env.data.items.map((t) => ({ equivalent: t.equivalent, from: t.from, to: t.to })),
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  __resetMockApiForTests()
})

describe('RT-013-6: mock trustline filters must answer what production answers', () => {
  it('a PARTIAL creditor pid returns nothing, because production resolves the pid exactly', async () => {
    installFixtures()

    // Production: `Participant.pid == 'p'` resolves nothing -> `list_all` returns [], `count_all` 0.
    expect(await listed({ creditor: 'p' })).toEqual({ total: 0, rows: [] })

    // Positive control: the WHOLE pid resolves, and only that creditor's rows come back.
    // This is what makes the assertion above evidence of exactness rather than of an
    // always-empty filter, and it also catches a filter applied to the wrong column.
    expect(await listed({ creditor: 'p1' })).toEqual({
      total: 2,
      rows: [
        { equivalent: 'USD', from: 'p1', to: 'p2' },
        { equivalent: 'EUR', from: 'p1', to: 'p4' },
      ],
    })
  })

  it('a PARTIAL debtor pid returns nothing; the whole pid returns exactly its rows', async () => {
    installFixtures()

    expect(await listed({ debtor: 'p' })).toEqual({ total: 0, rows: [] })

    expect(await listed({ debtor: 'p2' })).toEqual({
      total: 2,
      rows: [
        { equivalent: 'USD', from: 'p1', to: 'p2' },
        { equivalent: 'USD', from: 'p3', to: 'p2' },
      ],
    })
  })

  it('a lower-cased equivalent code returns nothing: no stored code can be lower-case', async () => {
    installFixtures()

    // `Equivalent.code` is `^[A-Z0-9_]{1,16}$`; `Equivalent.code == 'usd'` matches no row.
    expect(await listed({ equivalent: 'usd' })).toEqual({ total: 0, rows: [] })

    // Positive control: the code as stored resolves.
    expect(await listed({ equivalent: 'USD' })).toEqual({
      total: 2,
      rows: [
        { equivalent: 'USD', from: 'p1', to: 'p2' },
        { equivalent: 'USD', from: 'p3', to: 'p2' },
      ],
    })
  })

  it('a PARTIAL equivalent code returns nothing', async () => {
    installFixtures()

    // 'US' is a prefix of 'USD'; `Equivalent.code == 'US'` still matches nothing.
    expect(await listed({ equivalent: 'US' })).toEqual({ total: 0, rows: [] })
  })

  it('a space-padded pid returns nothing: nothing between the input and the SQL trims it', async () => {
    installFixtures()

    // `buildQuery` only drops empty strings, so ' p1 ' reaches
    // `Participant.pid == ' p1 '`, which resolves nothing.
    expect(await listed({ creditor: ' p1 ' })).toEqual({ total: 0, rows: [] })
  })

  it('filters still compose, and still narrow to the exact intersection', async () => {
    installFixtures()

    expect(await listed({ equivalent: 'USD', creditor: 'p1', debtor: 'p2', status: 'active' })).toEqual({
      total: 1,
      rows: [{ equivalent: 'USD', from: 'p1', to: 'p2' }],
    })
  })
})
