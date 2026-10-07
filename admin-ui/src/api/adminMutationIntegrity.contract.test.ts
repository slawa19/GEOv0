import { afterEach, describe, expect, it, vi } from 'vitest'

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
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(data)) as unknown as typeof fetch)
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

afterEach(() => {
  vi.unstubAllGlobals()
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
      // 032 S5 (F-4): the hold is cleared on the Integrity screen; the answer is the equivalent.
      name: 'integrity hold clear',
      data: equivalentWire({ code: 'UAH' }),
      call: () => realApi.clearIntegrityHold('UAH', 'reason'),
      expected: { code: 'UAH', precision: 2, description: 'Token', is_active: true },
    },
    {
      name: 'integrity summary',
      data: { equivalents: [{ equivalent: 'UAH', status: 'critical', checked_at: null, hold: true }] },
      call: () => realApi.integritySummary(),
      expected: { equivalents: [{ equivalent: 'UAH', status: 'critical', checked_at: null, hold: true }] },
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
  ])('accepts and normalizes valid $name data', async ({ data, call, expected }) => {
    useRealApiResponse(data)
    await expect(call()).resolves.toEqual(expected)
  })

  it.each([
    {
      name: 'participant action status outside freeze/unfreeze contract',
      data: { pid: 'PID_A', status: 'deleted' },
      call: () => realApi.freezeParticipant('PID_A', 'reason'),
    },
    {
      name: 'participant action extra field',
      data: { pid: 'PID_A', status: 'suspended', debug: true },
      call: () => realApi.freezeParticipant('PID_A', 'reason'),
    },
    {
      name: 'integrity hold clear invalid timestamp',
      data: equivalentWire({ code: 'UAH', updated_at: 'yesterday' }),
      call: () => realApi.clearIntegrityHold('UAH', 'reason'),
    },
    {
      name: 'integrity summary extra field',
      data: { equivalents: [{ equivalent: 'UAH', status: 'critical', checked_at: null, hold: true, debug: true }] },
      call: () => realApi.integritySummary(),
    },
    {
      name: 'integrity summary hold not boolean',
      data: { equivalents: [{ equivalent: 'UAH', status: 'critical', checked_at: null, hold: 'yes' }] },
      call: () => realApi.integritySummary(),
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
      name: 'equivalent create timestamp without timezone',
      data: equivalentWire({ created_at: '2026-08-08T11:00:00' }),
      call: () => realApi.createEquivalent({ code: 'TOK', precision: 2, description: 'Token' }),
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
  ])('rejects malformed $name 2xx data with INVALID_RESPONSE', async ({ data, call }) => {
    useRealApiResponse(data)
    await expect(call()).rejects.toMatchObject({ name: 'ApiException', code: 'INVALID_RESPONSE' })
  })
})

// 032 S4 (2026-10-07): the mock-client half of this file was removed with the mock client. What it
// asserted about the server is held by backend tests: response shapes - tests/integration/
// test_admin_mutation_audit_atomicity.py, test_admin_freeze_participant.py, test_admin_equivalent_
// input_validation.py, test_p024_equivalent_baseline_and_delete_postgres.py; refusal of a lower-case
// code and of precision 19 - test_admin_equivalent_input_validation.py; precision -1 and usage counts -
// tests/integration/test_p032_s4_admin_semantics_held_by_server.py.
