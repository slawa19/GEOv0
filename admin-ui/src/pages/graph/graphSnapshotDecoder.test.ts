/**
 * F-013-1 / T1302 - the decoder half, kept when 032 S5 (F-1) removed the activity card that read
 * these rows (moved here from `useGraphAnalytics.transactionCompleteness.test.ts`, whose consumer
 * half tested the removed client-side activity count).
 *
 * The graph page no longer asks for `include=transactions`, but `realApi.graphSnapshot` still
 * accepts the parameter and `GraphSnapshotSchema` still decodes the collection. While
 * `TransactionSchema` required `payload` - which neither the canon (`AdminGraphTransactionItem`)
 * declares nor `_graph_fetch_transactions` emits - the first response carrying a row was rejected
 * whole. Every row below is the shape the producer puts on the wire: no `payload`.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import { realApi } from '../../api/realApi'

const PID = 'PID_A'
const NOW = '2026-09-10T12:00:00Z'

/** One row exactly as `_graph_fetch_transactions` emits it. Note the absence of `payload`. */
function producerRow() {
  return {
    tx_id: 'tx-1',
    type: 'PAYMENT',
    state: 'COMMITTED',
    initiator_pid: PID,
    created_at: NOW,
    updated_at: NOW,
    equivalent: 'EUR',
    error: null,
  }
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
    equivalents: [{ code: 'EUR', precision: 2, description: 'EUR', is_active: true }],
    debts: [],
    audit_log: [],
    transactions: [],
    included: [],
    truncated: [],
    ...over,
  }
}

function devEnv() {
  const meta = import.meta as unknown as { env: Record<string, unknown> }
  meta.env.VITE_API_BASE_URL = ''
  meta.env.PROD = false
  meta.env.DEV = true
}

describe('graph snapshot decoder (F-013-1 / T1302)', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('sends include and accepts a producer-shaped transaction row without payload', async () => {
    devEnv()
    const fetchMock = vi.fn(async (_url: unknown) =>
      jsonResponse(snapshotBody({
        transactions: [producerRow()],
        included: ['transactions'],
        truncated: ['transactions'],
      })),
    )
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    const env = await realApi.graphSnapshot({ equivalent: 'EUR', include: ['transactions'] })

    expect(String(fetchMock.mock.calls[0]?.[0] ?? '')).toContain('include=transactions')
    expect(env.transactions).toHaveLength(1)
    expect(env.transactions[0]?.equivalent).toBe('EUR')
    expect(env.included).toEqual(['transactions'])
    expect(env.truncated).toEqual(['transactions'])
  })

  it('accepts a server that omits included/truncated: the canon does not mark them required', async () => {
    devEnv()
    const body = snapshotBody()
    delete (body as Record<string, unknown>).included
    delete (body as Record<string, unknown>).truncated
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(body)) as unknown as typeof fetch)

    const env = await realApi.graphSnapshot({ include: ['transactions'] })
    expect(env.included).toBeUndefined()
    expect(env.truncated).toBeUndefined()
  })
})
