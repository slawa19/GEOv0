import { afterEach, describe, expect, it, vi } from 'vitest'

import { assertSuccess } from './envelope'
import { __resetMockApiForTests, mockApi } from './mockApi'
import { realApi } from './realApi'

function jsonResponse(data: unknown): Response {
  return new Response(JSON.stringify(data), {
    status: 200,
    statusText: 'OK',
    headers: { 'Content-Type': 'application/json' },
  })
}

function useRealApiResponse(data: unknown) {
  const meta = import.meta as unknown as { env: Record<string, unknown> }
  meta.env.VITE_API_BASE_URL = ''
  meta.env.VITE_ADMIN_TOKEN = 'test-token'
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ success: true, data })) as unknown as typeof fetch)
}

const integrityStatus = {
  status: 'healthy',
  last_check: '2026-08-08T12:00:00Z',
  equivalents: {
    UAH: {
      status: 'healthy',
      checksum: 'abc',
      last_verified: null,
      invariants: {
        zero_sum: { passed: true, value: '0', violations: null, details: null },
      },
    },
  },
  alerts: [],
}

const integrityVerify = {
  status: 'healthy',
  checked_at: '2026-08-08T12:01:00Z',
  equivalents: integrityStatus.equivalents,
  alerts: [],
}

function equivalentWire(overrides: Record<string, unknown> = {}) {
  return {
    code: 'TOK',
    symbol: null,
    description: 'Token',
    precision: 2,
    metadata: null,
    is_active: true,
    created_at: '2026-08-08T11:00:00Z',
    updated_at: '2026-08-08T11:30:00Z',
    ...overrides,
  }
}

function useMockApiFixtures(overrides?: Record<string, unknown>) {
  const url = new URL('http://localhost/?scenario=happy')
  vi.stubGlobal('window', { ...window, location: url } as unknown as Window)

  const fixtures: Record<string, unknown> = {
    'scenarios/happy.json': { name: 'happy', latency_ms: { min: 0, max: 0 } },
    'datasets/participants.json': [
      { pid: 'PID_A', display_name: 'Alice', type: 'person', status: 'active' },
    ],
    'datasets/equivalents.json': [
      { code: 'UAH', precision: 2, description: 'Hryvnia', is_active: true },
    ],
    'datasets/trustlines.json': [],
    'datasets/debts.json': [],
    'datasets/audit-log.json': [],
    'datasets/integrity-status.json': integrityStatus,
    ...overrides,
  }

  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const requestUrl = String(input)
    const entry = Object.entries(fixtures).find(([path]) => requestUrl.includes(`/admin-fixtures/v1/${path}`))
    if (entry) return jsonResponse(entry[1])
    return new Response('Not Found', { status: 404, statusText: 'Not Found' })
  })
  vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
}

afterEach(() => {
  vi.unstubAllGlobals()
  __resetMockApiForTests()
})

describe('real Admin mutation and integrity response contracts', () => {
  it.each([
    {
      name: 'participant freeze',
      data: { pid: 'PID_A', status: 'suspended' },
      call: () => realApi.freezeParticipant('PID_A', 'reason'),
      expected: { pid: 'PID_A', status: 'suspended' },
    },
    {
      name: 'participant unfreeze',
      data: { pid: 'PID_A', status: 'active' },
      call: () => realApi.unfreezeParticipant('PID_A', 'reason'),
      expected: { pid: 'PID_A', status: 'active' },
    },
    {
      name: 'transaction abort',
      data: { tx_id: 'TX_1', status: 'aborted' },
      call: () => realApi.abortTx('TX_1', 'reason'),
      expected: { tx_id: 'TX_1', status: 'aborted' },
    },
    {
      name: 'equivalent create with nullable backend description',
      data: equivalentWire({ description: null }),
      call: () => realApi.createEquivalent({ code: 'TOK', precision: 2, description: '', is_active: true }),
      expected: { created: { code: 'TOK', precision: 2, description: '', is_active: true } },
    },
    {
      name: 'equivalent update',
      data: equivalentWire({ precision: 3 }),
      call: () => realApi.updateEquivalent('TOK', { precision: 3 }),
      expected: { updated: { code: 'TOK', precision: 3, description: 'Token', is_active: true } },
    },
    {
      name: 'equivalent active update',
      data: equivalentWire({ precision: 3, is_active: false }),
      call: () => realApi.setEquivalentActive('TOK', false, 'reason'),
      expected: { updated: { code: 'TOK', precision: 3, description: 'Token', is_active: false } },
    },
    {
      name: 'equivalent usage',
      data: { code: 'TOK', trustlines: 1, debts: 2, integrity_checkpoints: 3 },
      call: () => realApi.getEquivalentUsage('TOK'),
      expected: { code: 'TOK', trustlines: 1, debts: 2, integrity_checkpoints: 3 },
    },
    {
      name: 'equivalent delete',
      data: { deleted: 'TOK' },
      call: () => realApi.deleteEquivalent('TOK', 'reason'),
      expected: { deleted: 'TOK' },
    },
    {
      name: 'integrity status',
      data: integrityStatus,
      call: () => realApi.integrityStatus(),
      expected: integrityStatus,
    },
    {
      name: 'integrity verify',
      data: integrityVerify,
      call: () => realApi.integrityVerify(),
      expected: integrityVerify,
    },
    {
      name: 'integrity net repair',
      data: { ok: true, action: 'net-mutual-debts', netted_pairs: 1, updated: 1, deleted: 1 },
      call: () => realApi.integrityRepairNetMutualDebts(),
      expected: { ok: true, action: 'net-mutual-debts', netted_pairs: 1, updated: 1, deleted: 1 },
    },
    {
      name: 'integrity cap repair',
      data: { ok: true, action: 'cap-debts-to-trust-limits', scanned: 4, updated: 1, deleted: 2 },
      call: () => realApi.integrityRepairCapDebtsToTrustLimits(),
      expected: { ok: true, action: 'cap-debts-to-trust-limits', scanned: 4, updated: 1, deleted: 2 },
    },
  ])('accepts and normalizes valid $name data', async ({ data, call, expected }) => {
    useRealApiResponse(data)
    await expect(call()).resolves.toEqual({ success: true, data: expected })
  })

  it.each([
    {
      name: 'participant action status',
      data: { pid: 'PID_A', status: 'unknown' },
      call: () => realApi.freezeParticipant('PID_A', 'reason'),
    },
    {
      name: 'participant action extra field',
      data: { pid: 'PID_A', status: 'suspended', debug: true },
      call: () => realApi.freezeParticipant('PID_A', 'reason'),
    },
    {
      name: 'transaction abort extra field',
      data: { tx_id: 'TX_1', status: 'aborted', debug: true },
      call: () => realApi.abortTx('TX_1', 'reason'),
    },
    {
      name: 'equivalent create missing timestamp',
      data: equivalentWire({ created_at: undefined }),
      call: () => realApi.createEquivalent({ code: 'TOK', precision: 2, description: 'Token' }),
    },
    {
      name: 'equivalent update invalid code',
      data: equivalentWire({ code: 'tok-dash' }),
      call: () => realApi.updateEquivalent('TOK', { precision: 2 }),
    },
    {
      name: 'equivalent active update invalid timestamp',
      data: equivalentWire({ updated_at: 'yesterday' }),
      call: () => realApi.setEquivalentActive('TOK', false, 'reason'),
    },
    {
      name: 'equivalent delete extra field',
      data: { deleted: 'TOK', debug: true },
      call: () => realApi.deleteEquivalent('TOK', 'reason'),
    },
    {
      name: 'equivalent usage extra field',
      data: { code: 'TOK', trustlines: 0, debts: 0, integrity_checkpoints: 0, debug: true },
      call: () => realApi.getEquivalentUsage('TOK'),
    },
    {
      name: 'integrity status invalid date-time',
      data: { ...integrityStatus, last_check: 'yesterday' },
      call: () => realApi.integrityStatus(),
    },
    {
      name: 'integrity verify invalid date-time',
      data: { ...integrityVerify, checked_at: 'yesterday' },
      call: () => realApi.integrityVerify(),
    },
    {
      name: 'integrity nested last-verified invalid date-time',
      data: {
        ...integrityStatus,
        equivalents: {
          UAH: { ...integrityStatus.equivalents.UAH, last_verified: 'yesterday' },
        },
      },
      call: () => realApi.integrityStatus(),
    },
    {
      name: 'integrity net repair extra field',
      data: { ok: true, action: 'net-mutual-debts', netted_pairs: 0, updated: 0, deleted: 0, debug: true },
      call: () => realApi.integrityRepairNetMutualDebts(),
    },
    {
      name: 'integrity cap repair extra field',
      data: { ok: true, action: 'cap-debts-to-trust-limits', scanned: 0, updated: 0, deleted: 0, debug: true },
      call: () => realApi.integrityRepairCapDebtsToTrustLimits(),
    },
  ])('rejects malformed $name 2xx data with INVALID_RESPONSE', async ({ data, call }) => {
    useRealApiResponse(data)
    await expect(call()).rejects.toMatchObject({ name: 'ApiException', code: 'INVALID_RESPONSE' })
  })
})

describe('mock Admin mutation and integrity response contracts', () => {
  it('returns canonical participant, abort, equivalent and integrity mutation shapes', async () => {
    useMockApiFixtures()

    expect(assertSuccess(await mockApi.freezeParticipant('PID_A', 'reason'))).toEqual({
      pid: 'PID_A',
      status: 'suspended',
    })
    expect(assertSuccess(await mockApi.unfreezeParticipant('PID_A', 'reason'))).toEqual({
      pid: 'PID_A',
      status: 'active',
    })
    expect(assertSuccess(await mockApi.abortTx('TX_1', 'reason'))).toEqual({ tx_id: 'TX_1', status: 'aborted' })

    expect(
      assertSuccess(await mockApi.createEquivalent({ code: 'TOK', precision: 2, description: 'Token' })).created,
    ).toEqual({ code: 'TOK', precision: 2, description: 'Token', is_active: true })
    expect(assertSuccess(await mockApi.updateEquivalent('TOK', { precision: 3 })).updated).toEqual({
      code: 'TOK',
      precision: 3,
      description: 'Token',
      is_active: true,
    })
    expect(assertSuccess(await mockApi.setEquivalentActive('TOK', false, 'reason')).updated).toEqual({
      code: 'TOK',
      precision: 3,
      description: 'Token',
      is_active: false,
    })
    expect(assertSuccess(await mockApi.getEquivalentUsage('TOK'))).toEqual({
      code: 'TOK',
      trustlines: 0,
      debts: 0,
      integrity_checkpoints: 0,
    })
    expect(assertSuccess(await mockApi.deleteEquivalent('TOK', 'reason'))).toEqual({ deleted: 'TOK' })

    expect(assertSuccess(await mockApi.integrityStatus())).toEqual(integrityStatus)
    expect(assertSuccess(await mockApi.integrityVerify())).toMatchObject({
      status: 'healthy',
      equivalents: integrityStatus.equivalents,
      alerts: [],
    })
    expect(assertSuccess(await mockApi.integrityRepairNetMutualDebts())).toEqual({
      ok: true,
      action: 'net-mutual-debts',
      netted_pairs: 0,
      updated: 0,
      deleted: 0,
    })
    expect(assertSuccess(await mockApi.integrityRepairCapDebtsToTrustLimits())).toEqual({
      ok: true,
      action: 'cap-debts-to-trust-limits',
      scanned: 0,
      updated: 0,
      deleted: 0,
    })
  })

  it('rejects malformed equivalent and integrity fixture data with INVALID_RESPONSE', async () => {
    useMockApiFixtures({
      'datasets/equivalents.json': [
        { code: 'BAD-CODE', precision: 2, description: 'Hryvnia', is_active: true },
      ],
      'datasets/integrity-status.json': { ...integrityStatus, last_check: 'yesterday' },
    })

    await expect(mockApi.updateEquivalent('BAD-CODE', { description: 'Updated' })).rejects.toMatchObject({
      name: 'ApiException',
      code: 'INVALID_RESPONSE',
    })
    await expect(mockApi.integrityStatus()).rejects.toMatchObject({
      name: 'ApiException',
      code: 'INVALID_RESPONSE',
    })
  })
})
