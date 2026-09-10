/**
 * F-013-1 / T1302 - the client half.
 *
 * WHAT IS BEING PROVED. The graph page's Activity card used to answer "how many payments did this
 * participant commit in the last 7/30/90 days" with a confident `0 / 0 / 0` for a question it had
 * never asked the server. Three separate defects produced that number, and they only die together:
 *
 *   1. `graphSnapshot` built its query with `equivalent` alone, so `include=transactions` was never
 *      sent and the collection arrived empty on every load.
 *   2. `TransactionSchema` required `payload`, which neither the canon (`AdminGraphTransactionItem`)
 *      declares nor `_graph_fetch_transactions` emits. Invisible while the array was always empty;
 *      the first response carrying a row would have been rejected whole as INVALID_RESPONSE.
 *   3. The consumer read the equivalent from `tx.payload.equivalent` and derived "do we know
 *      anything about transactions" from `transactions.length`, which cannot tell "we did not ask"
 *      from "we asked and there are none".
 *
 * WHY THIS FILE BUILDS ITS ROWS BY HAND. `mockApi` returns transactions unconditionally AND with a
 * `payload` key, so a test written against the mock fixture is green under every one of the three
 * defects above and proves nothing. That trap is recorded in specs/013-frontend-data-honesty/spec.md
 * (reconnaissance note 4) as a forbidden method of proof. Every row below is the shape
 * `_graph_fetch_transactions` actually puts on the wire: tx_id, type, state, initiator_pid,
 * created_at, updated_at, equivalent, error - and no payload.
 */
import { describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'

vi.mock('../api', () => ({ api: { participantMetrics: vi.fn() } }))

import { useGraphAnalytics } from './useGraphAnalytics'
import { activityCounts } from '../pages/graph/graphPageHelpers'
import { realApi } from '../api/realApi'
import type { SelectedInfo } from './useGraphVisualization'
import type { AuditLogEntry, ClearingCycles, Debt, Incident, Participant, Transaction, Trustline } from '../pages/graph/graphTypes'

const PID = 'PID_A'
const NOW = '2026-09-10T12:00:00Z'

/** One row exactly as `_graph_fetch_transactions` emits it. Note the absence of `payload`. */
function producerRow(over: Partial<Transaction> = {}): Transaction {
  return {
    tx_id: 'tx-1',
    type: 'PAYMENT',
    state: 'COMMITTED',
    initiator_pid: PID,
    created_at: NOW,
    updated_at: NOW,
    equivalent: 'EUR',
    error: null,
    ...over,
  }
}

function analyticsFor(input: {
  transactions: Transaction[]
  included: string[]
  truncated: string[]
}) {
  return useGraphAnalytics({
    isRealMode: computed(() => true),
    threshold: ref('0.10'),
    analyticsEq: computed(() => 'EUR'),

    precisionByEq: computed(() => new Map([['EUR', 2]])),
    availableEquivalents: computed(() => ['EUR']),
    participantByPid: computed(() => new Map<string, Participant>()),

    participants: ref<Participant[]>([{ pid: PID, display_name: 'Alice' }]),
    trustlines: ref<Trustline[]>([]),
    debts: ref<Debt[]>([]),
    incidents: ref<Incident[]>([]),
    auditLog: ref<AuditLogEntry[]>([]),
    transactions: ref<Transaction[]>(input.transactions),
    included: ref<string[]>(input.included),
    truncated: ref<string[]>(input.truncated),
    clearingCycles: ref<ClearingCycles | null>(null),

    selected: ref<SelectedInfo | null>({ kind: 'node', pid: PID, degree: 0, inDegree: 0, outDegree: 0 }),
  })
}

function jsonResponse(obj: unknown): Response {
  return new Response(JSON.stringify(obj), {
    status: 200,
    statusText: 'OK',
    headers: { 'Content-Type': 'application/json' },
  })
}

function snapshotBody(over: Record<string, unknown> = {}) {
  return {
    participants: [{ pid: PID, display_name: 'Alice', type: 'person', status: 'active' }],
    trustlines: [],
    incidents: [],
    equivalents: [{ code: 'EUR', precision: 2, description: 'EUR', is_active: true }],
    debts: [],
    audit_log: [],
    transactions: [],
    included: [],
    truncated: [],
    ...over,
  }
}

describe('F-013-1 / T1302: transactions - "not asked" is not "zero"', () => {
  // ---------------------------------------------------------------------------------------------
  // The three states the card must be able to tell apart. All three carry an array; two of them
  // carry the SAME array, and only the completeness metadata separates them.
  // ---------------------------------------------------------------------------------------------

  it('NOT ASKED: an empty collection that was never included yields no knowledge and no number', () => {
    const g = analyticsFor({ transactions: [], included: [], truncated: [] })
    const a = g.selectedActivity.value
    expect(a).toBeTruthy()

    expect(a!.transactions.known).toBe(false)
    expect(a!.transactions.lowerBound).toBe(false)

    // The display side must not print a zero it cannot justify.
    expect(activityCounts(a!.paymentCommitted, a!.windows, a!.transactions)).toBe('— / — / —')
  })

  it('ASKED AND EMPTY: the same empty array, but named in `included`, is a real measurement of zero', () => {
    const g = analyticsFor({ transactions: [], included: ['transactions'], truncated: [] })
    const a = g.selectedActivity.value

    // Byte-identical `transactions: []` to the case above, opposite conclusion. This is the pair
    // the old `transactions.length > 0` could not distinguish, and the whole of the finding.
    expect(a!.transactions.known).toBe(true)
    expect(a!.transactions.lowerBound).toBe(false)
    expect(a!.transactions.incomplete).toBe(false)
    expect(a!.paymentCommitted[7]).toBe(0)
    expect(activityCounts(a!.paymentCommitted, a!.windows, a!.transactions)).toBe('0 / 0 / 0')
  })

  it('ASKED AND TRUNCATED: counts over a cut list are lower bounds and are printed as such', () => {
    const g = analyticsFor({
      transactions: [
        producerRow({ tx_id: 'tx-1' }),
        producerRow({ tx_id: 'tx-2' }),
      ],
      included: ['transactions'],
      truncated: ['transactions'],
    })
    const a = g.selectedActivity.value

    expect(a!.transactions.known).toBe(true)
    expect(a!.transactions.lowerBound).toBe(true)
    expect(a!.paymentCommitted[7]).toBe(2)

    // The server returned a prefix of a longer list, so 2 is "at least 2" and must not read as a
    // total. Without this the card presents a lower bound in the typography of a fact.
    expect(activityCounts(a!.paymentCommitted, a!.windows, a!.transactions)).toBe('≥2 / ≥2 / ≥2')
  })

  it('`truncated` without `included` is ignored: a cut we were never told we received says nothing', () => {
    const g = analyticsFor({ transactions: [], included: [], truncated: ['transactions'] })
    const a = g.selectedActivity.value
    expect(a!.transactions.known).toBe(false)
    expect(a!.transactions.lowerBound).toBe(false)
  })

  // ---------------------------------------------------------------------------------------------
  // Where the consumer reads the equivalent from.
  // ---------------------------------------------------------------------------------------------

  it('reads the equivalent from the top level of the row, where the producer puts it', () => {
    const g = analyticsFor({
      transactions: [
        producerRow({ tx_id: 'eur', equivalent: 'EUR' }),
        producerRow({ tx_id: 'uah', equivalent: 'UAH' }),
      ],
      included: ['transactions'],
      truncated: [],
    })
    const a = g.selectedActivity.value

    // The selected equivalent is EUR. Exactly one of these two rows belongs to it. Reading
    // `tx.payload.equivalent` - which is undefined on every producer row - would have let both rows
    // through the filter and counted the UAH payment as a EUR one.
    expect(a!.paymentCommitted[7]).toBe(1)
  })

  it('does not invent involvement: a payment initiated by someone else is not this participant\'s', () => {
    const g = analyticsFor({
      transactions: [producerRow({ tx_id: 'other', initiator_pid: 'PID_B' })],
      included: ['transactions'],
      truncated: [],
    })
    const a = g.selectedActivity.value

    // The row carries no counterparties, so it cannot be attributed to PID_A - and it cannot be
    // ruled out either. The count therefore stays at 0 AND is marked incomplete, so nothing prints
    // it as a total.
    //
    // The projection the spec prescribes - `from`/`to` for a PAYMENT, `edges` for a CLEARING - IS
    // on the wire (`_graph_fetch_transactions`, 2026-09-10), and a row that carries it does go
    // quiet. What this row proves is the remaining case: the producer emits each key only when the
    // internal payload holds a non-empty value, so a row without them is still reachable and still
    // has to be admitted rather than counted as a zero. The reads themselves are covered in
    // `useGraphAnalytics.activityConfidence.test.ts` - deliberately not here, because every row in
    // THIS file comes from a `producerRow()` that never sets those three fields, which is exactly
    // why both reads could once be reverted with the whole suite green.
    expect(a!.paymentCommitted[7]).toBe(0)
    expect(a!.transactions.incomplete).toBe(true)
    expect(activityCounts(a!.paymentCommitted, a!.windows, a!.transactions)).toBe('— / — / —')
  })

  // ---------------------------------------------------------------------------------------------
  // The wire. Two halves of the same atomic change: the request must carry `include`, and the
  // decoder must accept what that request brings back.
  // ---------------------------------------------------------------------------------------------

  it('graphSnapshot asks for transactions, and the decoder accepts a producer-shaped row', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    meta.env.PROD = false
    meta.env.DEV = true

    const fetchMock = vi.fn(async (_url: unknown) =>
      jsonResponse({
        success: true,
        data: snapshotBody({
          transactions: [producerRow()],
          included: ['transactions'],
          truncated: ['transactions'],
        }),
      }),
    )
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    const env = await realApi.graphSnapshot({ equivalent: 'EUR', include: ['transactions'] })

    const url = String(fetchMock.mock.calls[0]?.[0] ?? '')
    expect(url).toContain('include=transactions')

    // The row above has no `payload`. While TransactionSchema required one, this call rejected the
    // WHOLE response and the graph page blanked - which is why the include and the schema could
    // never have shipped as two commits.
    expect(env.success).toBe(true)
    if (!env.success) return
    expect(env.data.transactions).toHaveLength(1)
    expect(env.data.transactions[0]?.equivalent).toBe('EUR')
    expect(env.data.included).toEqual(['transactions'])
    expect(env.data.truncated).toEqual(['transactions'])
  })

  it('a server that omits included/truncated is read as "we were told nothing", not as "all present"', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''
    meta.env.PROD = false
    meta.env.DEV = true

    const body = snapshotBody()
    delete (body as Record<string, unknown>).included
    delete (body as Record<string, unknown>).truncated

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ success: true, data: body })) as unknown as typeof fetch,
    )

    // The canon does not mark these two fields required, so their absence is a legal response and
    // must not be a decode failure - the mistake TransactionSchema used to make with `payload`.
    const env = await realApi.graphSnapshot({ include: ['transactions'] })
    expect(env.success).toBe(true)
    if (!env.success) return
    expect(env.data.included).toBeUndefined()

    const g = analyticsFor({ transactions: [], included: [], truncated: [] })
    expect(g.selectedActivity.value!.transactions.known).toBe(false)
  })
})
