import { afterEach, describe, expect, it, vi } from 'vitest'

import { assertSuccess } from './envelope'
import { __resetMockApiForTests, mockApi } from './mockApi'

const config = {
  LOG_LEVEL: 'INFO',
  RATE_LIMIT_ENABLED: true,
  ROUTING_MAX_HOPS: 6,
  ROUTING_MAX_PATHS: 3,
  INTEGRITY_CHECKPOINT_ENABLED: true,
  INTEGRITY_CHECKPOINT_INTERVAL_SECONDS: 300,
  RECOVERY_ENABLED: true,
  RECOVERY_INTERVAL_SECONDS: 60,
  PAYMENT_TX_STUCK_TIMEOUT_SECONDS: 120,
  FEATURE_FLAGS_MULTIPATH_ENABLED: true,
  FEATURE_FLAGS_FULL_MULTIPATH_ENABLED: false,
  CLEARING_ENABLED: true,
}

const flags = {
  multipath_enabled: true,
  full_multipath_enabled: false,
  clearing_enabled: true,
}

const incidents = {
  items: [{
    tx_id: 'TX_STUCK_1',
    state: 'PREPARE_IN_PROGRESS',
    initiator_pid: 'PID_A',
    equivalent: 'UAH',
    age_seconds: 300,
    sla_seconds: 120,
  }],
}

function jsonResponse(data: unknown): Response {
  return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

function installFixtures() {
  const mockWindow = Object.create(window) as Window
  Object.defineProperty(mockWindow, 'location', { value: new URL('http://localhost/?scenario=happy') })
  vi.stubGlobal('window', mockWindow)
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/scenarios/happy.json')) return jsonResponse({ name: 'happy', latency_ms: { min: 0, max: 0 } })
    if (url.endsWith('/datasets/config.json')) return jsonResponse(config)
    if (url.endsWith('/datasets/feature-flags.json')) return jsonResponse(flags)
    if (url.endsWith('/datasets/audit-log.json')) return jsonResponse([])
    if (url.endsWith('/datasets/incidents.json')) return jsonResponse(incidents)
    if (url.endsWith('/datasets/participants.json')) return jsonResponse([])
    if (url.endsWith('/datasets/equivalents.json')) return jsonResponse([])
    if (url.endsWith('/datasets/trustlines.json')) return jsonResponse([])
    if (url.endsWith('/datasets/debts.json')) return jsonResponse([])
    if (url.endsWith('/datasets/transactions.json')) return jsonResponse([])
    return new Response('Not Found', { status: 404 })
  }) as unknown as typeof fetch)
}

function transactionFixture(txId: string, state = 'WAITING') {
  return {
    tx_id: txId,
    type: 'PAYMENT',
    initiator_pid: 'PID_A',
    payload: {},
    state,
    error: null,
    created_at: '2026-08-08T12:00:00Z',
    updated_at: '2026-08-08T12:00:00Z',
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  __resetMockApiForTests()
})

describe('mock Admin mutation state and audit contracts', () => {
  it('records canonical config and feature-flag audit entries', async () => {
    installFixtures()

    assertSuccess(await mockApi.patchConfig({ ROUTING_MAX_PATHS: 4 }))
    assertSuccess(await mockApi.patchFeatureFlags({ clearing_enabled: false }))
    const audit = assertSuccess(await mockApi.listAuditLog({ page: 1, per_page: 10 }))

    expect(audit.items.map((entry) => entry.action)).toEqual([
      'admin.feature_flags.patch',
      'admin.config.patch',
    ])
    expect(audit.items[1]).toMatchObject({
      object_type: 'config',
      object_id: null,
      before_state: { ROUTING_MAX_PATHS: 3 },
      after_state: { ROUTING_MAX_PATHS: 4 },
    })
    expect(audit.items[0]).toMatchObject({
      object_type: 'feature_flags',
      before_state: { clearing_enabled: true },
      after_state: { clearing_enabled: false },
    })
    expect(audit.items.every((entry) => /^[0-9a-f-]{36}$/i.test(entry.id) && Boolean(entry.timestamp))).toBe(true)

    const snapshot = assertSuccess(await mockApi.graphSnapshot())
    expect(snapshot.audit_log.map((entry) => entry.action)).toEqual([
      'admin.feature_flags.patch',
      'admin.config.patch',
    ])
  })

  it('removes an aborted terminal incident from subsequent reloads and records the canonical action', async () => {
    installFixtures()

    expect(assertSuccess(await mockApi.listIncidents({})).items.map((item) => item.tx_id)).toEqual(['TX_STUCK_1'])
    expect(assertSuccess(await mockApi.abortTx('TX_STUCK_1', 'operator recovery'))).toEqual({
      tx_id: 'TX_STUCK_1',
      status: 'aborted',
    })
    expect(assertSuccess(await mockApi.listIncidents({})).items).toEqual([])

    const audit = assertSuccess(await mockApi.listAuditLog({ action: 'admin.transactions.abort' }))
    expect(audit.items).toHaveLength(1)
    expect(audit.items[0]).toMatchObject({
      action: 'admin.transactions.abort',
      object_type: 'transaction',
      object_id: 'TX_STUCK_1',
      after_state: { state: 'ABORTED' },
    })

    expect(assertSuccess(await mockApi.abortTx('TX_STUCK_1', 'repeat recovery'))).toEqual({
      tx_id: 'TX_STUCK_1',
      status: 'aborted',
    })
    const repeatedAudit = assertSuccess(await mockApi.listAuditLog({ action: 'admin.transactions.abort' }))
    expect(repeatedAudit.items).toHaveLength(2)
    expect(repeatedAudit.items[0]).toMatchObject({
      before_state: { state: 'ABORTED', error: null },
      after_state: { state: 'ABORTED' },
      reason: 'repeat recovery',
    })
  })

  it('rejects an unknown transaction without fabricating an audit entry', async () => {
    installFixtures()

    await expect(mockApi.abortTx('TX_UNKNOWN', 'operator recovery')).resolves.toEqual({
      success: false,
      error: { code: 'NOT_FOUND', message: 'transaction not found' },
    })
    expect(assertSuccess(await mockApi.listAuditLog({})).items).toEqual([])
  })

  it('updates the cached transaction state and abort reason', async () => {
    installFixtures()
    const tx = transactionFixture('TX_WAITING')
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/scenarios/happy.json')) return jsonResponse({ name: 'happy', latency_ms: { min: 0, max: 0 } })
      if (url.endsWith('/datasets/transactions.json')) return jsonResponse([tx])
      if (url.endsWith('/datasets/audit-log.json')) return jsonResponse([])
      if (url.endsWith('/datasets/incidents.json')) return new Response('Not Found', { status: 404 })
      if (url.endsWith('/datasets/participants.json')) return jsonResponse([])
      if (url.endsWith('/datasets/trustlines.json')) return jsonResponse([])
      if (url.endsWith('/datasets/equivalents.json')) return jsonResponse([])
      if (url.endsWith('/datasets/debts.json')) return jsonResponse([])
      return new Response('Not Found', { status: 404 })
    })

    assertSuccess(await mockApi.abortTx('TX_WAITING', 'operator recovery'))
    const snapshot = assertSuccess(await mockApi.graphSnapshot())
    expect(snapshot.transactions[0]).toMatchObject({
      tx_id: 'TX_WAITING',
      state: 'ABORTED',
      error: { code: 'E010', message: 'operator recovery', details: {} },
    })
  })

  it('does not mutate incident, transaction, or config state when audit validation fails', async () => {
    installFixtures()
    const tx = transactionFixture('TX_STUCK_1')
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/scenarios/happy.json')) return jsonResponse({ name: 'happy', latency_ms: { min: 0, max: 0 } })
      if (url.endsWith('/datasets/incidents.json')) return jsonResponse(incidents)
      if (url.endsWith('/datasets/transactions.json')) return jsonResponse([tx])
      if (url.endsWith('/datasets/config.json')) return jsonResponse(config)
      if (url.endsWith('/datasets/feature-flags.json')) return jsonResponse(flags)
      if (url.endsWith('/datasets/participants.json')) {
        return jsonResponse([{ pid: 'PID_A', display_name: 'Alice', type: 'person', status: 'active' }])
      }
      if (url.endsWith('/datasets/equivalents.json')) return jsonResponse([])
      if (url.endsWith('/datasets/trustlines.json')) return jsonResponse([])
      if (url.endsWith('/datasets/debts.json')) return jsonResponse([])
      if (url.endsWith('/datasets/audit-log.json')) return jsonResponse({ malformed: true })
      return new Response('Not Found', { status: 404 })
    })

    await expect(mockApi.abortTx('TX_STUCK_1', 'operator recovery')).rejects.toMatchObject({
      code: 'INVALID_RESPONSE',
    })
    expect(assertSuccess(await mockApi.listIncidents({})).items.map((item) => item.tx_id)).toEqual(['TX_STUCK_1'])
    expect(assertSuccess(await mockApi.graphSnapshot()).transactions[0]).toMatchObject({ state: 'WAITING', error: null })

    await expect(mockApi.patchConfig({ ROUTING_MAX_PATHS: 4 })).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
    expect(assertSuccess(await mockApi.getConfig()).ROUTING_MAX_PATHS).toBe(3)

    await expect(mockApi.patchFeatureFlags({ clearing_enabled: false })).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
    expect(assertSuccess(await mockApi.getFeatureFlags()).clearing_enabled).toBe(true)

    await expect(mockApi.freezeParticipant('PID_A', 'operator recovery')).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
    expect(assertSuccess(await mockApi.listParticipants({})).items[0]?.status).toBe('active')

    await expect(mockApi.createEquivalent({ code: 'TOK', precision: 2, description: 'Token' })).rejects.toMatchObject({
      code: 'INVALID_RESPONSE',
    })
    expect(assertSuccess(await mockApi.listEquivalents({ include_inactive: true })).items).toEqual([])
  })

  it('serializes concurrent aborts so audit and incident state cannot diverge', async () => {
    installFixtures()
    const concurrentIncidents = {
      items: [
        { ...incidents.items[0], tx_id: 'TX_A' },
        { ...incidents.items[0], tx_id: 'TX_B' },
      ],
    }
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/scenarios/happy.json')) return jsonResponse({ name: 'happy', latency_ms: { min: 0, max: 0 } })
      if (url.endsWith('/datasets/incidents.json')) return jsonResponse(concurrentIncidents)
      if (url.endsWith('/datasets/transactions.json')) return jsonResponse([])
      if (url.endsWith('/datasets/audit-log.json')) return jsonResponse([])
      return new Response('Not Found', { status: 404 })
    })

    const [first, second] = await Promise.all([
      mockApi.abortTx('TX_A', 'abort A'),
      mockApi.abortTx('TX_B', 'abort B'),
    ])
    expect(assertSuccess(first).status).toBe('aborted')
    expect(assertSuccess(second).status).toBe('aborted')
    expect(assertSuccess(await mockApi.listIncidents({})).items).toEqual([])
    const audit = assertSuccess(await mockApi.listAuditLog({ action: 'admin.transactions.abort' }))
    expect(audit.items).toHaveLength(2)
    expect(new Set(audit.items.map((entry) => entry.object_id))).toEqual(new Set(['TX_A', 'TX_B']))
  })

  it('shares one audit-load owner between a Graph snapshot and a concurrent mutation', async () => {
    installFixtures()
    let resolveAudit!: (response: Response) => void
    const auditResponse = new Promise<Response>((resolve) => {
      resolveAudit = resolve
    })
    let auditRequests = 0
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/scenarios/happy.json')) return jsonResponse({ name: 'happy', latency_ms: { min: 0, max: 0 } })
      if (url.endsWith('/datasets/audit-log.json')) {
        auditRequests += 1
        return auditResponse
      }
      if (url.endsWith('/datasets/config.json')) return jsonResponse(config)
      if (url.endsWith('/datasets/participants.json')) return jsonResponse([])
      if (url.endsWith('/datasets/trustlines.json')) return jsonResponse([])
      if (url.endsWith('/datasets/equivalents.json')) return jsonResponse([])
      if (url.endsWith('/datasets/incidents.json')) return new Response('Not Found', { status: 404 })
      if (url.endsWith('/datasets/debts.json')) return new Response('Not Found', { status: 404 })
      if (url.endsWith('/datasets/transactions.json')) return new Response('Not Found', { status: 404 })
      return new Response('Not Found', { status: 404 })
    })

    const graphPromise = mockApi.graphSnapshot()
    const mutationPromise = mockApi.patchConfig({ ROUTING_MAX_PATHS: 4 })
    await vi.waitFor(() => expect(auditRequests).toBe(1))
    resolveAudit(jsonResponse([]))

    const [graphEnvelope] = await Promise.all([graphPromise, mutationPromise])
    expect(auditRequests).toBe(1)
    expect(assertSuccess(graphEnvelope).audit_log.map((entry) => entry.action)).toEqual(['admin.config.patch'])
    expect(assertSuccess(await mockApi.listAuditLog({})).items.map((entry) => entry.action)).toEqual([
      'admin.config.patch',
    ])
  })

  it('retries an optional incident fixture after a transient failure', async () => {
    installFixtures()
    let incidentAttempts = 0
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/scenarios/happy.json')) return jsonResponse({ name: 'happy', latency_ms: { min: 0, max: 0 } })
      if (url.endsWith('/datasets/incidents.json')) {
        incidentAttempts += 1
        return incidentAttempts === 1
          ? new Response('temporary', { status: 503 })
          : jsonResponse(incidents)
      }
      return new Response('Not Found', { status: 404 })
    })

    expect(assertSuccess(await mockApi.listIncidents({})).items).toEqual([])
    expect(assertSuccess(await mockApi.listIncidents({})).items.map((item) => item.tx_id)).toEqual(['TX_STUCK_1'])
  })

  it('keeps graph snapshot available when its optional audit fixture is malformed', async () => {
    installFixtures()
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/scenarios/happy.json')) return jsonResponse({ name: 'happy', latency_ms: { min: 0, max: 0 } })
      if (url.endsWith('/datasets/audit-log.json')) return jsonResponse({ malformed: true })
      if (url.endsWith('/datasets/participants.json')) return jsonResponse([])
      if (url.endsWith('/datasets/trustlines.json')) return jsonResponse([])
      if (url.endsWith('/datasets/equivalents.json')) return jsonResponse([])
      if (url.endsWith('/datasets/incidents.json')) return new Response('Not Found', { status: 404 })
      if (url.endsWith('/datasets/debts.json')) return new Response('Not Found', { status: 404 })
      if (url.endsWith('/datasets/transactions.json')) return new Response('Not Found', { status: 404 })
      return new Response('Not Found', { status: 404 })
    })

    expect(assertSuccess(await mockApi.graphSnapshot()).audit_log).toEqual([])
  })

  it('treats a missing Graph audit fixture as one silent optional attempt', async () => {
    installFixtures()
    let auditRequests = 0
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/scenarios/happy.json')) return jsonResponse({ name: 'happy', latency_ms: { min: 0, max: 0 } })
      if (url.endsWith('/datasets/audit-log.json')) {
        auditRequests += 1
        return new Response('Not Found', { status: 404 })
      }
      if (url.endsWith('/datasets/participants.json')) return jsonResponse([])
      if (url.endsWith('/datasets/trustlines.json')) return jsonResponse([])
      if (url.endsWith('/datasets/equivalents.json')) return jsonResponse([])
      return new Response('Not Found', { status: 404 })
    })

    expect(assertSuccess(await mockApi.graphSnapshot()).audit_log).toEqual([])
    expect(auditRequests).toBe(1)
  })

  it('rejects aborting a transaction proven committed by the transaction fixture', async () => {
    installFixtures()
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/scenarios/happy.json')) return jsonResponse({ name: 'happy', latency_ms: { min: 0, max: 0 } })
      if (url.endsWith('/datasets/incidents.json')) return new Response('Not Found', { status: 404 })
      if (url.endsWith('/datasets/transactions.json')) {
        return jsonResponse([{
          tx_id: 'TX_COMMITTED',
          type: 'PAYMENT',
          initiator_pid: 'PID_A',
          payload: {},
          state: 'COMMITTED',
          created_at: '2026-08-08T12:00:00Z',
          updated_at: '2026-08-08T12:00:00Z',
        }])
      }
      if (url.endsWith('/datasets/audit-log.json')) return jsonResponse([])
      return new Response('Not Found', { status: 404 })
    })

    await expect(mockApi.abortTx('TX_COMMITTED', 'operator recovery')).resolves.toEqual({
      success: false,
      error: { code: 'CONFLICT', message: 'Transaction is already committed' },
    })
    expect(assertSuccess(await mockApi.listAuditLog({})).items).toEqual([])
  })
})
