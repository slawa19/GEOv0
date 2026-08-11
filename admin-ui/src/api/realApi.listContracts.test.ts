import { afterEach, describe, expect, it, vi } from 'vitest'

import { realApi } from './realApi'

vi.mock('./errorToast', () => ({ toastApiError: vi.fn() }))

function respondWith(payload: unknown): void {
  const meta = import.meta as unknown as { env: Record<string, unknown> }
  meta.env.VITE_API_BASE_URL = ''
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

const participant = {
  pid: 'alice',
  display_name: 'Alice',
  type: 'person',
  status: 'active',
  verification_level: 1,
  created_at: '2026-08-12T00:00:00Z',
}

const trustline = {
  equivalent: 'UAH',
  from: 'alice',
  to: 'bob',
  limit: '100.00000000',
  used: '5.00000000',
  available: '95.00000000',
  status: 'active',
  created_at: '2026-08-12T00:00:00Z',
  policy: null,
}

const auditEntry = {
  id: 'audit-1',
  timestamp: '2026-08-12T00:00:00Z',
  actor_id: null,
  actor_role: 'admin',
  action: 'test.action',
  object_type: null,
  object_id: null,
  reason: null,
  request_id: null,
  ip_address: null,
  user_agent: null,
}

const equivalent = {
  code: 'UAH',
  precision: 2,
  description: null,
  is_active: true,
}

const incident = {
  tx_id: 'tx-1',
  state: 'PREPARED',
  initiator_pid: 'alice',
  equivalent: 'UAH',
  age_seconds: 61,
  sla_seconds: 60,
  created_at: null,
}

describe('realApi list response contracts', () => {
  it.each([
    {
      label: 'participants',
      payload: { items: [participant], page: 1, per_page: 20, total: 1 },
      call: () => realApi.listParticipants({ page: 1, per_page: 20 }),
    },
    {
      label: 'trustlines',
      payload: { items: [trustline], page: 1, per_page: 20, total: 1 },
      call: () => realApi.listTrustlines({ page: 1, per_page: 20 }),
    },
    {
      label: 'audit log',
      payload: { items: [auditEntry], page: 1, per_page: 50, total: 1 },
      call: () => realApi.listAuditLog({ page: 1, per_page: 50 }),
    },
    {
      label: 'equivalents',
      payload: { items: [equivalent] },
      call: () => realApi.listEquivalents({ include_inactive: true }),
    },
    {
      label: 'incidents',
      payload: { items: [incident], page: 1, per_page: 20, total: 1 },
      call: () => realApi.listIncidents({ page: 1, per_page: 20 }),
    },
  ])('accepts canonical $label payload', async ({ payload, call }) => {
    respondWith(payload)
    await expect(call()).resolves.toMatchObject({ success: true })
  })

  it.each([
    {
      label: 'participants missing total',
      payload: { items: [participant], page: 1, per_page: 20 },
      call: () => realApi.listParticipants({ page: 1, per_page: 20 }),
    },
    {
      label: 'trustline decimal field',
      payload: { items: [{ ...trustline, limit: false }], page: 1, per_page: 20, total: 1 },
      call: () => realApi.listTrustlines({ page: 1, per_page: 20 }),
    },
    {
      label: 'trustline nullable policy type',
      payload: { items: [{ ...trustline, policy: 42 }], page: 1, per_page: 20, total: 1 },
      call: () => realApi.listTrustlines({ page: 1, per_page: 20 }),
    },
    {
      label: 'audit item identity',
      payload: { items: [{ ...auditEntry, id: 1 }], page: 1, per_page: 50, total: 1 },
      call: () => realApi.listAuditLog({ page: 1, per_page: 50 }),
    },
    {
      label: 'audit nullable actor type',
      payload: { items: [{ ...auditEntry, actor_id: 42 }], page: 1, per_page: 50, total: 1 },
      call: () => realApi.listAuditLog({ page: 1, per_page: 50 }),
    },
    {
      label: 'equivalent precision',
      payload: { items: [{ ...equivalent, precision: '2' }] },
      call: () => realApi.listEquivalents({ include_inactive: true }),
    },
    {
      label: 'equivalent nullable description type',
      payload: { items: [{ ...equivalent, description: 42 }] },
      call: () => realApi.listEquivalents({ include_inactive: true }),
    },
    {
      label: 'incidents pagination',
      payload: { items: [incident], page: '1', per_page: 20, total: 1 },
      call: () => realApi.listIncidents({ page: 1, per_page: 20 }),
    },
    {
      label: 'incident nullable created_at type',
      payload: { items: [{ ...incident, created_at: 42 }], page: 1, per_page: 20, total: 1 },
      call: () => realApi.listIncidents({ page: 1, per_page: 20 }),
    },
  ])('rejects malformed 2xx $label payload', async ({ payload, call }) => {
    respondWith(payload)
    await expect(call()).rejects.toMatchObject({
      name: 'ApiException',
      code: 'INVALID_RESPONSE',
      status: 200,
    })
  })
})
